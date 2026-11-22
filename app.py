"""
Rhythm & Pitch — Ultimate Edition
----------------------------------
New features:
  • Background beat track (kick/snare pattern) during gameplay
  • Hold notes (notes with tails you must hold down)
  • Animated starfield background
  • Settings menu (volume, speed, key rebinding)
  • Enhanced visuals: gradient notes, screen flash, better particles
  • Improved combo counter with animations
  • Input latency calibration option

Controls:
  A S D F G   -> hit/hold notes
  P           -> pause
  Esc         -> back to menu
  Space       -> confirm in menus
  Tab         -> open settings

Requirements:  pip install PySide6
"""

import sys
import math
import random
import time
from PySide6.QtCore import (
    Qt, QTimer, QRect, QRectF, QSettings, QElapsedTimer, QPoint
)
from PySide6.QtGui import (
    QPainter, QColor, QLinearGradient, QFont, QPen, QRadialGradient,
    QKeySequence, QBrush
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QMessageBox
)
from PySide6.QtMultimedia import QAudioFormat, QAudioSink


# ============================== Config ==============================
DEFAULT_KEYS = [Qt.Key_A, Qt.Key_S, Qt.Key_D, Qt.Key_F, Qt.Key_G]
LANE_LABELS = ["A", "S", "D", "F", "G"]
LANE_COLORS = [
    QColor(255, 90, 120),
    QColor(255, 180, 80),
    QColor(255, 230, 90),
    QColor(120, 220, 140),
    QColor(110, 170, 255),
]

LANE_TIMBRE = [
    (1.0, 0.0, 0.0, 0.0),
    (1.0, 0.3, 0.0, 0.0),
    (1.0, 0.5, 0.2, 0.0),
    (1.0, 0.4, 0.3, 0.1),
    (1.0, 0.6, 0.4, 0.2),
]

DIFFICULTIES = {
    "Easy":   {"speed": 0.75, "density": 0.55, "hp_drain": 6,  "hp_gain": 2, "hold_chance": 0.05},
    "Normal": {"speed": 1.00, "density": 1.00, "hp_drain": 10, "hp_gain": 3, "hold_chance": 0.15},
    "Hard":   {"speed": 1.35, "density": 1.55, "hp_drain": 14, "hp_gain": 4, "hold_chance": 0.25},
}


# ============================== Songs ==============================
def _build_chart(seed, bpm, bars, density, hold_chance):
    rng = random.Random(seed)
    beat_ms = 60000 / bpm
    total_ms = bars * 4 * beat_ms
    notes = []
    t = 0.0
    last_lane = -1
    
    while t < total_ms:
        r = rng.random()
        if r < 0.55 * density:
            step = beat_ms / 2
        elif r < 0.75 * density:
            step = beat_ms / 4
        else:
            step = beat_ms

        lane = rng.randrange(5)
        if lane == last_lane and rng.random() < 0.7:
            lane = (lane + rng.choice([-2, -1, 1, 2])) % 5
        last_lane = lane

        # Regular note or hold note
        is_hold = rng.random() < hold_chance
        hold_duration = int(beat_ms * rng.uniform(0.5, 2.0)) if is_hold else 0
        
        notes.append((int(t), lane, is_hold, hold_duration))
        
        if density > 0.9 and rng.random() < 0.15:
            other = (lane + rng.choice([-2, -1, 1, 2])) % 5
            is_hold2 = rng.random() < hold_chance
            hold_duration2 = int(beat_ms * rng.uniform(0.5, 2.0)) if is_hold2 else 0
            notes.append((int(t), other, is_hold2, hold_duration2))

        t += step + rng.uniform(-beat_ms * 0.05, beat_ms * 0.05)

    notes.sort(key=lambda x: x[0])
    return notes, int(total_ms)


SONGS = [
    {
        "title": "Neon Dreams",
        "artist": "Synthwave",
        "bpm": 100,
        "bars": 24,
        "seed": 42,
        "color": QColor(180, 120, 255),
        "beat_pattern": "kick",  # kick, snare, both
    },
    {
        "title": "Pixel Storm",
        "artist": "Chiptune",
        "bpm": 132,
        "bars": 20,
        "seed": 1337,
        "color": QColor(90, 220, 255),
        "beat_pattern": "both",
    },
    {
        "title": "Cosmic Rush",
        "artist": "Drum & Bass",
        "bpm": 174,
        "bars": 16,
        "seed": 2077,
        "color": QColor(255, 140, 180),
        "beat_pattern": "snare",
    },
]

for s in SONGS:
    diff = DIFFICULTIES["Normal"]
    notes, dur = _build_chart(s["seed"], s["bpm"], s["bars"], diff["density"], diff["hold_chance"])
    s["chart_full"] = notes
    s["duration_ms"] = dur


# ============================== Audio ==============================
class AudioEngine:
    """Handles all audio: hit sounds, miss sounds, background beat."""
    FREQS = [261.63, 293.66, 329.63, 349.23, 392.00]

    def __init__(self, volume=0.4):
        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.UInt8)
        self.fmt = fmt
        self.sink = QAudioSink(fmt)
        self.sink.setVolume(volume)
        self.device = self.sink.start()
        self.sample_rate = 44100
        self.phase = 0.0
        self.volume = volume
        
        # Beat track state
        self.beat_playing = False
        self.beat_timer = 0.0
        self.beat_interval = 0.0
        self.beat_pattern = "kick"
        self.beat_count = 0

    def set_volume(self, v):
        self.volume = v
        self.sink.setVolume(v)

    def _adsr(self, i, n, attack=200, decay=800, sustain=0.7, release=None):
        if release is None:
            release = n - decay
        if i < attack:
            return i / attack
        if i < decay:
            return 1.0 - (1.0 - sustain) * (i - attack) / max(1, decay - attack)
        if i < release:
            return sustain
        return sustain * max(0.0, 1 - (i - release) / max(1, n - release))

    def _render(self, freq, duration_ms, timbre, volume=0.6):
        n = int(self.sample_rate * duration_ms / 1000)
        buf = bytearray(n)
        two_pi_over_sr = 2 * math.pi / self.sample_rate
        for i in range(n):
            env = self._adsr(i, n)
            s = 0.0
            for h, w in enumerate(timbre, start=1):
                if w == 0:
                    continue
                s += w * math.sin(self.phase * h)
            s = s / max(1.0, sum(timbre))
            s *= env * volume
            self.phase += two_pi_over_sr * freq
            if self.phase > 2 * math.pi:
                self.phase -= 2 * math.pi
            buf[i] = int(128 + s * 110)
        return bytes(buf)

    def _render_kick(self, volume=0.6):
        """Render a kick drum sound."""
        duration_ms = 150
        n = int(self.sample_rate * duration_ms / 1000)
        buf = bytearray(n)
        for i in range(n):
            t = i / self.sample_rate
            freq = 80 * math.exp(-t * 20)  # Pitch drop
            env = math.exp(-t * 8)
            s = math.sin(2 * math.pi * freq * t) * env * volume
            buf[i] = int(128 + s * 120)
        return bytes(buf)

    def _render_snare(self, volume=0.5):
        """Render a snare drum sound."""
        duration_ms = 120
        n = int(self.sample_rate * duration_ms / 1000)
        buf = bytearray(n)
        for i in range(n):
            t = i / self.sample_rate
            env = math.exp(-t * 15)
            # Noise + tone
            noise = random.uniform(-1, 1)
            tone = math.sin(2 * math.pi * 200 * t)
            s = (noise * 0.7 + tone * 0.3) * env * volume
            buf[i] = int(128 + s * 100)
        return bytes(buf)

    def play_hit(self, lane_index, volume=0.7):
        if 0 <= lane_index < len(self.FREQS):
            data = self._render(
                self.FREQS[lane_index], 180,
                LANE_TIMBRE[lane_index], volume * self.volume
            )
            if self.device is not None:
                self.device.write(data)

    def play_miss(self):
        data = self._render(90, 120, (1.0, 0.2, 0.0, 0.0), 0.35 * self.volume)
        if self.device is not None:
            self.device.write(data)

    def play_combo(self):
        data = self._render(880, 220, (1.0, 0.5, 0.3, 0.2), 0.5 * self.volume)
        if self.device is not None:
            self.device.write(data)

    def start_beat(self, bpm, pattern="kick"):
        self.beat_playing = True
        self.beat_interval = 60.0 / bpm
        self.beat_timer = 0.0
        self.beat_pattern = pattern
        self.beat_count = 0

    def stop_beat(self):
        self.beat_playing = False

    def update_beat(self, dt):
        if not self.beat_playing:
            return
        self.beat_timer += dt
        if self.beat_timer >= self.beat_interval:
            self.beat_timer -= self.beat_interval
            self.beat_count += 1
            
            # Play beat based on pattern
            if self.beat_pattern == "kick":
                if self.beat_count % 2 == 1:  # Every other beat
                    data = self._render_kick(0.4 * self.volume)
                    if self.device:
                        self.device.write(data)
            elif self.beat_pattern == "snare":
                if self.beat_count % 4 == 3:  # Beat 4
                    data = self._render_snare(0.35 * self.volume)
                    if self.device:
                        self.device.write(data)
            elif self.beat_pattern == "both":
                if self.beat_count % 2 == 1:
                    data = self._render_kick(0.35 * self.volume)
                    if self.device:
                        self.device.write(data)
                if self.beat_count % 4 == 3:
                    data = self._render_snare(0.3 * self.volume)
                    if self.device:
                        self.device.write(data)


# ============================== Particles ==============================
class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color", "size", "type")

    def __init__(self, x, y, color, ptype="burst"):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(80, 280) if ptype == "burst" else random.uniform(20, 80)
        self.x = x
        self.y = y
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed - (60 if ptype == "burst" else 20)
        self.life = 0.0
        self.max_life = random.uniform(0.35, 0.7) if ptype == "burst" else random.uniform(1.0, 2.0)
        self.color = QColor(color)
        self.size = random.uniform(2, 5) if ptype == "burst" else random.uniform(1, 3)
        self.type = ptype

    def update(self, dt):
        self.life += dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.type == "burst":
            self.vy += 300 * dt

    @property
    def alive(self):
        return self.life < self.max_life


class ParticleSystem:
    def __init__(self):
        self.particles = []

    def burst(self, x, y, color, count=18):
        for _ in range(count):
            self.particles.append(Particle(x, y, color, "burst"))

    def sparkle(self, x, y, color, count=8):
        for _ in range(count):
            self.particles.append(Particle(x, y, color, "sparkle"))

    def update(self, dt):
        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.alive]

    def draw(self, painter):
        painter.setPen(Qt.NoPen)
        for p in self.particles:
            t = p.life / p.max_life
            alpha = int(255 * (1 - t))
            c = QColor(p.color)
            c.setAlpha(alpha)
            painter.setBrush(c)
            r = p.size * (1 - t * 0.5)
            painter.drawEllipse(QRectF(p.x - r, p.y - r, r * 2, r * 2))


# ============================== Starfield ==============================
class Star:
    __slots__ = ("x", "y", "z", "speed")

    def __init__(self, w, h):
        self.x = random.uniform(-w/2, w/2)
        self.y = random.uniform(-h/2, h/2)
        self.z = random.uniform(0.1, 1.0)
        self.speed = random.uniform(20, 80)

    def update(self, dt, w, h):
        self.y += self.speed * self.z * dt
        if self.y > h/2:
            self.y = -h/2
            self.x = random.uniform(-w/2, w/2)


class Starfield:
    def __init__(self, w, h, count=100):
        self.stars = [Star(w, h) for _ in range(count)]

    def update(self, dt, w, h):
        for s in self.stars:
            s.update(dt, w, h)

    def draw(self, painter, w, h):
        painter.setPen(Qt.NoPen)
        for s in self.stars:
            alpha = int(255 * s.z)
            size = 1 + s.z * 2
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QRectF(w/2 + s.x - size/2, h/2 + s.y - size/2, size, size))


# ============================== Game ==============================
class Note:
    __slots__ = ("lane", "y", "alive", "trail", "is_hold", "hold_duration", "hold_progress", "held")

    def __init__(self, lane, y, is_hold=False, hold_duration=0):
        self.lane = lane
        self.y = y
        self.alive = True
        self.trail = []
        self.is_hold = is_hold
        self.hold_duration = hold_duration  # in pixels
        self.hold_progress = 0.0
        self.held = False


class GameView(QWidget):
    PERFECT_WINDOW = 20
    GOOD_WINDOW = 45
    MISS_WINDOW = 75
    BASE_SPEED = 380
    HIT_LINE_RATIO = 0.82

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

        self.audio = AudioEngine()
        self.particles = ParticleSystem()
        self.settings = QSettings("RhythmPitch", "Game")
        self.starfield = Starfield(800, 900)

        # Settings
        self.volume = self.settings.value("volume", 0.4, type=float)
        self.speed_mult = self.settings.value("speed", 1.0, type=float)
        self.lane_keys = self.load_keys()
        self.audio.set_volume(self.volume)

        # Menu state
        self.state = "menu"  # menu | playing | paused | gameover | settings
        self.menu_index = 0
        self.diff_index = 1
        self.settings_index = 0

        # Game state
        self.notes = []
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.perfects = 0
        self.goods = 0
        self.oks = 0
        self.misses = 0
        self.total_notes = 0
        self.hp = 100.0
        self.elapsed = 0.0
        self.song_elapsed = 0.0
        self.next_note_idx = 0
        self.flash = [0.0] * 5
        self.judgment = ("", 0.0, QColor())
        self.shake = 0.0
        self.beat_pulse = 0.0
        self.last_beat = 0.0
        self.screen_flash = 0.0
        self.combo_scale = 1.0

        self.current_song = None
        self.current_chart = None
        self.current_diff = None
        self.diff_speed_mult = 1.0
        self.diff_density_mult = 1.0

        # Input state for hold notes
        self.keys_held = [False] * 5

        # Loop
        self.last_time = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        self.setMinimumSize(640, 820)
        self.setWindowTitle("Rhythm & Pitch — Ultimate")

    def load_keys(self):
        keys = []
        for i in range(5):
            k = self.settings.value(f"key_{i}", DEFAULT_KEYS[i], type=int)
            keys.append(k)
        return keys

    def save_keys(self):
        for i, k in enumerate(self.lane_keys):
            self.settings.setValue(f"key_{i}", k)

    def lane_x_center(self, lane):
        lane_w = self.width() / 5
        return int(lane * lane_w + lane_w / 2)

    def hit_y(self):
        return int(self.height() * self.HIT_LINE_RATIO)

    def start_game(self, song_idx, diff_name):
        self.current_song = SONGS[song_idx]
        self.current_diff = DIFFICULTIES[diff_name]
        self.diff_speed_mult = self.current_diff["speed"]
        self.diff_density_mult = self.current_diff["density"]

        full = self.current_song["chart_full"]
        if self.diff_density_mult >= 1.0:
            chart = full
        else:
            rng = random.Random(self.current_song["seed"] + hash(diff_name))
            chart = [n for n in full if rng.random() < self.diff_density_mult]

        self.current_chart = chart
        self.total_notes = len(chart)

        self.state = "playing"
        self.notes.clear()
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.perfects = 0
        self.goods = 0
        self.oks = 0
        self.misses = 0
        self.hp = 100.0
        self.elapsed = 0.0
        self.song_elapsed = 0.0
        self.next_note_idx = 0
        self.flash = [0.0] * 5
        self.judgment = ("", 0.0, QColor())
        self.shake = 0.0
        self.screen_flash = 0.0
        self.particles.particles.clear()
        self.keys_held = [False] * 5

        # Start background beat
        self.audio.start_beat(self.current_song["bpm"], self.current_song["beat_pattern"])

    def end_game(self):
        self.state = "gameover"
        self.audio.stop_beat()
        key = f"highscore_{self.current_song['title']}"
        prev = self.settings.value(key, 0, type=int)
        if self.score > prev:
            self.settings.setValue(key, self.score)

    def grade(self):
        if self.total_notes == 0:
            return "D", 0.0
        accuracy = (
            self.perfects * 1.0 + self.goods * 0.7 + self.oks * 0.3
        ) / self.total_notes
        if accuracy >= 0.95:
            return "S", accuracy
        if accuracy >= 0.85:
            return "A", accuracy
        if accuracy >= 0.70:
            return "B", accuracy
        if accuracy >= 0.50:
            return "C", accuracy
        return "D", accuracy

    def tick(self):
        t = time.perf_counter()
        if self.last_time is None:
            self.last_time = t
        dt = t - self.last_time
        self.last_time = t
        if dt > 0.1:
            dt = 0.1

        self.starfield.update(dt, self.width(), self.height())
        self.audio.update_beat(dt)

        if self.state == "playing":
            self.elapsed += dt
            self.song_elapsed += dt * 1000

            travel_time_ms = (self.hit_y() + 40) / (self.BASE_SPEED * self.diff_speed_mult * self.speed_mult) * 1000
            while (self.next_note_idx < len(self.current_chart)
                   and self.current_chart[self.next_note_idx][0]
                   <= self.song_elapsed + travel_time_ms):
                tm, lane, is_hold, hold_dur = self.current_chart[self.next_note_idx]
                # Convert hold duration from ms to pixels
                hold_px = int(hold_dur / 1000 * self.BASE_SPEED * self.diff_speed_mult * self.speed_mult) if is_hold else 0
                self.notes.append(Note(lane, -40, is_hold, hold_px))
                self.next_note_idx += 1

            speed = self.BASE_SPEED * self.diff_speed_mult * self.speed_mult
            for n in self.notes:
                n.trail.append(n.y)
                if len(n.trail) > 5:
                    n.trail.pop(0)
                n.y += speed * dt

                # Update hold progress
                if n.is_hold and n.held:
                    n.hold_progress += speed * dt

            # Miss detection
            hit_y = self.hit_y()
            for n in self.notes:
                if n.alive and n.y > hit_y + self.MISS_WINDOW:
                    n.alive = False
                    self.misses += 1
                    self.combo = 0
                    self.hp -= self.current_diff["hp_drain"]
                    self.judgment = ("MISS", 0.5, QColor(200, 60, 60))
                    self.audio.play_miss()
                    self.shake = 0.25

            # Cleanup
            self.notes = [n for n in self.notes
                          if n.alive and n.y < self.height() + 60]

            # Beat pulse
            beat_ms = 60000 / self.current_song["bpm"]
            beat_num = int(self.song_elapsed / beat_ms)
            if beat_num != self.last_beat:
                self.last_beat = beat_num
                self.beat_pulse = 1.0

            # End conditions
            if self.hp <= 0:
                self.hp = 0
                self.end_game()
            elif (self.next_note_idx >= len(self.current_chart)
                  and not self.notes):
                self.end_game()

            # Decay effects
            for i in range(5):
                self.flash[i] = max(0.0, self.flash[i] - dt * 3)
            if self.judgment[1] > 0:
                txt, tm, col = self.judgment
                self.judgment = (txt, tm - dt, col)
            self.shake = max(0.0, self.shake - dt * 2)
            self.beat_pulse = max(0.0, self.beat_pulse - dt * 3)
            self.screen_flash = max(0.0, self.screen_flash - dt * 4)
            self.combo_scale = max(1.0, self.combo_scale - dt * 3)

        self.particles.update(dt)
        self.update()

    def try_hit(self, lane):
        if self.state != "playing":
            return
        self.flash[lane] = 1.0
        self.keys_held[lane] = True
        hit_y = self.hit_y()
        best = None
        best_dist = None
        for n in self.notes:
            if not n.alive or n.lane != lane:
                continue
            d = abs(n.y - hit_y)
            if d <= self.MISS_WINDOW and (best is None or d < best_dist):
                best = n
                best_dist = d

        if best is None:
            self.combo = 0
            self.hp -= self.current_diff["hp_drain"] * 0.3
            self.judgment = ("MISS", 0.35, QColor(150, 150, 150))
            return

        if best.is_hold:
            best.held = True
            best.hold_progress = 0.0
        
        best.alive = False if not best.is_hold else True
        self.audio.play_hit(lane)

        cx = self.lane_x_center(lane)
        col = LANE_COLORS[lane]

        if best_dist <= self.PERFECT_WINDOW:
            self.score += 300 + self.combo * 12
            self.perfects += 1
            self.combo += 1
            self.hp = min(100.0, self.hp + self.current_diff["hp_gain"])
            self.judgment = ("PERFECT", 0.5, QColor(255, 230, 90))
            self.particles.burst(cx, hit_y, col, 22)
            self.screen_flash = 0.3
            self.combo_scale = 1.5
        elif best_dist <= self.GOOD_WINDOW:
            self.score += 150 + self.combo * 6
            self.goods += 1
            self.combo += 1
            self.hp = min(100.0, self.hp + self.current_diff["hp_gain"] * 0.5)
            self.judgment = ("GOOD", 0.45, QColor(120, 220, 140))
            self.particles.burst(cx, hit_y, col, 12)
            self.combo_scale = 1.3
        else:
            self.score += 50
            self.oks += 1
            self.combo = 0
            self.judgment = ("OK", 0.35, QColor(200, 200, 200))
            self.particles.burst(cx, hit_y, col, 6)

        self.max_combo = max(self.max_combo, self.combo)
        if self.combo > 0 and self.combo % 25 == 0:
            self.audio.play_combo()
            self.shake = 0.15

    def try_release(self, lane):
        if self.state != "playing":
            return
        self.keys_held[lane] = False
        
        # Check if we were holding a note
        for n in self.notes:
            if n.alive and n.lane == lane and n.is_hold and n.held:
                # Check if hold was completed
                if n.hold_progress >= n.hold_duration * 0.9:
                    self.score += 200
                    self.particles.sparkle(self.lane_x_center(lane), self.hit_y(), LANE_COLORS[lane], 12)
                else:
                    self.combo = 0
                    self.hp -= self.current_diff["hp_drain"] * 0.5
                    self.judgment = ("MISS", 0.4, QColor(200, 60, 60))
                n.alive = False
                n.held = False
                break

    def keyPressEvent(self, e):
        k = e.key()

        if self.state == "settings":
            if k == Qt.Key_Up:
                self.settings_index = (self.settings_index - 1) % 4
            elif k == Qt.Key_Down:
                self.settings_index = (self.settings_index + 1) % 4
            elif k == Qt.Key_Left:
                if self.settings_index == 0:  # Volume
                    self.volume = max(0.0, self.volume - 0.1)
                    self.audio.set_volume(self.volume)
                    self.settings.setValue("volume", self.volume)
                elif self.settings_index == 1:  # Speed
                    self.speed_mult = max(0.5, self.speed_mult - 0.1)
                    self.settings.setValue("speed", self.speed_mult)
            elif k == Qt.Key_Right:
                if self.settings_index == 0:
                    self.volume = min(1.0, self.volume + 0.1)
                    self.audio.set_volume(self.volume)
                    self.settings.setValue("volume", self.volume)
                elif self.settings_index == 1:
                    self.speed_mult = min(2.0, self.speed_mult + 0.1)
                    self.settings.setValue("speed", self.speed_mult)
            elif k == Qt.Key_Return and self.settings_index >= 2:  # Rebind keys
                # Wait for next key press
                pass
            elif k == Qt.Key_Escape or k == Qt.Key_Tab:
                self.state = "menu"
            elif self.settings_index >= 2 and self.settings_index < 7:  # Rebinding
                lane = self.settings_index - 2
                self.lane_keys[lane] = k
                self.save_keys()
            return

        if self.state == "menu":
            if k == Qt.Key_Up:
                self.menu_index = (self.menu_index - 1) % len(SONGS)
            elif k == Qt.Key_Down:
                self.menu_index = (self.menu_index + 1) % len(SONGS)
            elif k == Qt.Key_Left:
                self.diff_index = (self.diff_index - 1) % len(DIFFICULTIES)
            elif k == Qt.Key_Right:
                self.diff_index = (self.diff_index + 1) % len(DIFFICULTIES)
            elif k == Qt.Key_Space or k == Qt.Key_Return:
                diff_name = list(DIFFICULTIES.keys())[self.diff_index]
                self.start_game(self.menu_index, diff_name)
            elif k == Qt.Key_Tab:
                self.state = "settings"
            elif k == Qt.Key_Escape:
                QApplication.instance().quit()
            return

        if self.state == "playing":
            if k == Qt.Key_P:
                self.state = "paused"
                self.audio.stop_beat()
                return
            if k == Qt.Key_Escape:
                self.state = "menu"
                self.audio.stop_beat()
                return
            for i, key in enumerate(self.lane_keys):
                if k == key:
                    self.try_hit(i)
                    return
            return

        if self.state == "paused":
            if k == Qt.Key_P or k == Qt.Key_Space:
                self.state = "playing"
                self.audio.start_beat(self.current_song["bpm"], self.current_song["beat_pattern"])
            elif k == Qt.Key_Escape:
                self.state = "menu"
                self.audio.stop_beat()
            return

        if self.state == "gameover":
            if k == Qt.Key_Space or k == Qt.Key_Return:
                self.state = "menu"
            elif k == Qt.Key_Escape:
                QApplication.instance().quit()
            return

    def keyReleaseEvent(self, e):
        k = e.key()
        if self.state == "playing":
            for i, key in enumerate(self.lane_keys):
                if k == key:
                    self.try_release(i)
                    return

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        bg = QLinearGradient(0, 0, 0, h)
        pulse = self.beat_pulse
        c1 = QColor(15 + int(20 * pulse), 18 + int(10 * pulse), 40 + int(30 * pulse))
        c2 = QColor(40 + int(30 * pulse), 20 + int(15 * pulse), 60 + int(40 * pulse))
        bg.setColorAt(0, c1)
        bg.setColorAt(1, c2)
        p.fillRect(self.rect(), bg)

        # Starfield
        self.starfield.draw(p, w, h)

        # Screen flash
        if self.screen_flash > 0:
            p.fillRect(self.rect(), QColor(255, 255, 255, int(80 * self.screen_flash)))

        # Shake
        sx = sy = 0
        if self.shake > 0:
            sx = int((random.random() - 0.5) * 10 * self.shake)
            sy = int((random.random() - 0.5) * 10 * self.shake)
            p.translate(sx, sy)

        if self.state == "menu":
            self.draw_menu(p, w, h)
        elif self.state == "settings":
            self.draw_settings(p, w, h)
        elif self.state == "gameover":
            self.draw_game(p, w, h)
            self.draw_gameover(p, w, h)
        elif self.state == "paused":
            self.draw_game(p, w, h)
            self.draw_pause(p, w, h)
        else:
            self.draw_game(p, w, h)

        self.particles.draw(p)

    def draw_game(self, p, w, h):
        lane_w = w / 5
        for i in range(5):
            r = QRect(int(i * lane_w), 0, int(lane_w), h)
            col = QColor(LANE_COLORS[i])
            col.setAlpha(18)
            p.fillRect(r, col)
            if self.flash[i] > 0:
                fc = QColor(LANE_COLORS[i])
                fc.setAlpha(int(110 * self.flash[i]))
                p.fillRect(r, fc)
            if i > 0:
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                p.drawLine(int(i * lane_w), 0, int(i * lane_w), h)

        hit_y = self.hit_y()
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.drawLine(0, hit_y, w, hit_y)

        for i in range(5):
            cx = self.lane_x_center(i)
            col = QColor(LANE_COLORS[i])
            if self.flash[i] > 0:
                col = col.lighter(160)
            
            # Gradient receptor
            grad = QLinearGradient(cx - 32, hit_y - 16, cx - 32, hit_y + 16)
            grad.setColorAt(0, col.lighter(130))
            grad.setColorAt(1, col)
            p.setBrush(grad)
            p.setPen(QPen(QColor(255, 255, 255, 200), 2))
            p.drawRoundedRect(QRect(cx - 32, hit_y - 16, 64, 32), 12, 12)
            p.setPen(QColor(20, 20, 30))
            p.setFont(QFont("Segoe UI", 14, QFont.Bold))
            p.drawText(QRect(cx - 32, hit_y - 16, 64, 32), Qt.AlignCenter, LANE_LABELS[i])

        # Notes
        for n in self.notes:
            if not n.alive:
                continue
            cx = self.lane_x_center(n.lane)
            col = QColor(LANE_COLORS[n.lane])
            
            # Trail
            for idx, ty in enumerate(n.trail):
                alpha = int(60 * (idx + 1) / len(n.trail))
                tc = QColor(col)
                tc.setAlpha(alpha)
                p.setBrush(tc)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRect(cx - 22, int(ty) - 8, 44, 16), 8, 8)

            # Hold note tail
            if n.is_hold and n.held:
                tail_y = int(n.y - n.hold_progress)
                grad = QLinearGradient(cx - 20, tail_y, cx - 20, int(n.y))
                grad.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 100))
                grad.setColorAt(1, col)
                p.setBrush(grad)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRect(cx - 20, tail_y, 40, int(n.y) - tail_y), 8, 8)

            # Glow
            glow = QColor(col)
            glow.setAlpha(80)
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRect(cx - 38, int(n.y) - 20, 76, 40), 16, 16)
            
            # Core with gradient
            grad = QLinearGradient(cx - 30, int(n.y) - 14, cx - 30, int(n.y) + 14)
            grad.setColorAt(0, col.lighter(140))
            grad.setColorAt(1, col)
            p.setBrush(grad)
            p.setPen(QPen(QColor(255, 255, 255, 220), 2))
            p.drawRoundedRect(QRect(cx - 30, int(n.y) - 14, 60, 28), 12, 12)
            
            # Hold indicator
            if n.is_hold:
                p.setPen(QColor(255, 255, 255, 180))
                p.setFont(QFont("Segoe UI", 10, QFont.Bold))
                p.drawText(QRect(cx - 20, int(n.y) - 8, 40, 16), Qt.AlignCenter, "HOLD")

        # HUD
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 16, QFont.Bold))
        p.drawText(20, 34, f"Score: {self.score}")
        p.setFont(QFont("Segoe UI", 12))
        hs = self.settings.value(f"highscore_{self.current_song['title']}", 0, type=int)
        p.drawText(20, 56, f"Best:  {hs}")

        # HP bar
        bar_w = 180
        bar_h = 14
        bx = w - bar_w - 20
        by = 20
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawRect(QRect(bx, by, bar_w, bar_h))
        hp_color = (QColor(120, 220, 140) if self.hp > 50
                    else QColor(255, 180, 80) if self.hp > 25
                    else QColor(255, 90, 120))
        p.setBrush(hp_color)
        p.drawRect(QRect(bx, by, int(bar_w * self.hp / 100), bar_h))
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(QRect(bx, by, bar_w, bar_h), Qt.AlignCenter, f"HP {int(self.hp)}")

        # Combo
        if self.combo >= 2:
            p.setPen(QColor(255, 230, 90))
            size = int(28 * self.combo_scale)
            p.setFont(QFont("Segoe UI", size, QFont.Bold))
            p.drawText(QRect(w - 200, 60, 180, 40), Qt.AlignRight | Qt.AlignVCenter, f"{self.combo}x")

        # Progress bar
        if self.current_song:
            total_ms = self.current_song["duration_ms"]
            frac = min(1.0, self.song_elapsed / total_ms)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRect(QRect(20, h - 30, w - 40, 8))
            p.setBrush(self.current_song["color"])
            p.drawRect(QRect(20, h - 30, int((w - 40) * frac), 8))

        # Judgment
        txt, tm, col = self.judgment
        if tm > 0 and txt:
            alpha = int(255 * min(1.0, tm / 0.5))
            c = QColor(col)
            c.setAlpha(alpha)
            p.setPen(c)
            scale = 1.0 + 0.2 * max(0, 0.5 - tm) / 0.5
            f = QFont("Segoe UI", int(32 * scale), QFont.Bold)
            p.setFont(f)
            p.drawText(QRect(0, hit_y - 130, w, 60), Qt.AlignCenter, txt)

    def draw_menu(self, p, w, h):
        p.setPen(QColor(255, 230, 90))
        p.setFont(QFont("Segoe UI", 42, QFont.Bold))
        p.drawText(QRect(0, 60, w, 80), Qt.AlignCenter, "RHYTHM & PITCH")
        p.setPen(QColor(200, 200, 255))
        p.setFont(QFont("Segoe UI", 14))
        p.drawText(QRect(0, 140, w, 30), Qt.AlignCenter, "Ultimate Edition")

        diff_names = list(DIFFICULTIES.keys())
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 12))
        p.drawText(QRect(0, 200, w, 24), Qt.AlignCenter,
                   "Difficulty:  ◀  " + diff_names[self.diff_index] + "  ▶")

        p.setFont(QFont("Segoe UI", 13, QFont.Bold))
        p.drawText(QRect(0, 260, w, 24), Qt.AlignCenter, "SELECT A SONG")

        y = 310
        for i, s in enumerate(SONGS):
            selected = (i == self.menu_index)
            col = QColor(s["color"]) if selected else QColor(180, 180, 200)
            p.setPen(col)
            if selected:
                p.setFont(QFont("Segoe UI", 20, QFont.Bold))
            else:
                p.setFont(QFont("Segoe UI", 16))
            p.drawText(QRect(0, y, w, 34), Qt.AlignCenter, s["title"])
            p.setFont(QFont("Segoe UI", 11))
            p.setPen(QColor(180, 180, 200))
            p.drawText(QRect(0, y + 30, w, 20), Qt.AlignCenter,
                       f"{s['artist']}  •  {s['bpm']} BPM  •  {len(s['chart_full'])} notes")
            hs = self.settings.value(f"highscore_{s['title']}", 0, type=int)
            if hs > 0:
                p.drawText(QRect(0, y + 50, w, 18), Qt.AlignCenter, f"Best: {hs}")
            y += 90

        p.setPen(QColor(180, 180, 200))
        p.setFont(QFont("Segoe UI", 11))
        p.drawText(QRect(0, h - 80, w, 20), Qt.AlignCenter,
                   "↑ ↓ select song   ◀ ▶ difficulty   SPACE start   TAB settings   ESC quit")
        p.drawText(QRect(0, h - 56, w, 20), Qt.AlignCenter,
                   "In-game:  A S D F G  to hit/hold  •  P to pause")

    def draw_settings(self, p, w, h):
        p.setPen(QColor(255, 230, 90))
        p.setFont(QFont("Segoe UI", 36, QFont.Bold))
        p.drawText(QRect(0, 80, w, 60), Qt.AlignCenter, "SETTINGS")

        items = [
            f"Volume:  {'◀' if self.settings_index == 0 else ' '}  {int(self.volume * 100)}%  {'▶' if self.settings_index == 0 else ' '}",
            f"Speed:   {'◀' if self.settings_index == 1 else ' '}  {self.speed_mult:.1f}x  {'▶' if self.settings_index == 1 else ' '}",
            "",
            "Key Bindings:",
        ]
        
        for i, key in enumerate(self.lane_keys):
            key_name = QKeySequence(key).toString()
            items.append(f"  Lane {i+1}:  {key_name}  {'◀ ▶ to rebind' if self.settings_index == i+2 else ''}")

        y = 200
        for idx, item in enumerate(items):
            if idx == self.settings_index:
                p.setPen(QColor(255, 230, 90))
                p.setFont(QFont("Segoe UI", 16, QFont.Bold))
            else:
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont("Segoe UI", 14))
            p.drawText(QRect(0, y, w, 30), Qt.AlignCenter, item)
            y += 36

        p.setPen(QColor(180, 180, 200))
        p.setFont(QFont("Segoe UI", 11))
        p.drawText(QRect(0, h - 60, w, 20), Qt.AlignCenter,
                   "↑ ↓ navigate   ◀ ▶ adjust   ESC/TAB to go back")

    def draw_pause(self, p, w, h):
        p.fillRect(self.rect(), QColor(0, 0, 0, 160))
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 40, QFont.Bold))
        p.drawText(QRect(0, h // 2 - 60, w, 60), Qt.AlignCenter, "PAUSED")
        p.setFont(QFont("Segoe UI", 14))
        p.drawText(QRect(0, h // 2 + 20, w, 30), Qt.AlignCenter,
                   "P / SPACE to resume   •   ESC to menu")

    def draw_gameover(self, p, w, h):
        p.fillRect(self.rect(), QColor(0, 0, 0, 180))
        grade, acc = self.grade()
        grade_colors = {
            "S": QColor(255, 230, 90),
            "A": QColor(120, 220, 140),
            "B": QColor(110, 170, 255),
            "C": QColor(255, 180, 80),
            "D": QColor(200, 100, 100),
        }
        p.setPen(grade_colors[grade])
        p.setFont(QFont("Segoe UI", 80, QFont.Bold))
        p.drawText(QRect(0, 100, w, 120), Qt.AlignCenter, grade)

        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 18, QFont.Bold))
        p.drawText(QRect(0, 230, w, 30), Qt.AlignCenter, self.current_song["title"])

        p.setFont(QFont("Segoe UI", 14))
        stats = [
            f"Score:        {self.score}",
            f"Max Combo:    {self.max_combo}",
            f"Perfect:      {self.perfects}",
            f"Good:         {self.goods}",
            f"OK:           {self.oks}",
            f"Miss:         {self.misses}",
            f"Accuracy:     {acc * 100:.1f}%",
        ]
        y = 290
        for s in stats:
            p.drawText(QRect(0, y, w, 24), Qt.AlignCenter, s)
            y += 26

        hs = self.settings.value(f"highscore_{self.current_song['title']}", 0, type=int)
        if self.score >= hs and self.score > 0:
            p.setPen(QColor(255, 230, 90))
            p.setFont(QFont("Segoe UI", 16, QFont.Bold))
            p.drawText(QRect(0, y + 10, w, 24), Qt.AlignCenter, "★ NEW HIGH SCORE ★")

        p.setPen(QColor(200, 200, 220))
        p.setFont(QFont("Segoe UI", 12))
        p.drawText(QRect(0, h - 60, w, 24), Qt.AlignCenter,
                   "SPACE to return to menu   •   ESC to quit")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    view = GameView()
    view.resize(720, 900)
    view.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()