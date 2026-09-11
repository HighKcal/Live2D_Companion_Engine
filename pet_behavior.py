"""Time-based behavior, independent of Qt rendering and disk I/O."""
from collections import deque
import math
import random


def smooth(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


class Relocator:
    RESTING = 'resting'
    FADING_OUT = 'fading_out'
    RELOCATING = 'relocating'
    FADING_IN = 'fading_in'

    def __init__(self, profile, now, rng=None):
        self.profile = profile
        self.rng = rng or random.Random()
        self.state = self.RESTING
        self.destination = None
        self.phase_started = now
        self.fade_out_duration = 0.0
        self.fade_in_duration = 0.0
        self.pause(now)

    @property
    def active(self):
        return self.state != self.RESTING

    def pause(self, now):
        self.state = self.RESTING
        self.destination = None
        self.phase_started = now
        self.due = now + self.rng.uniform(*self.profile['rest_seconds'])

    def _choose_destination(self, position, limits, size):
        left, top, right, bottom = limits
        x, y = position
        settings = self.profile['relocation']
        candidates = []
        for _ in range(settings['destination_attempts']):
            point = (self.rng.uniform(left, right), self.rng.uniform(top, bottom))
            candidates.append((math.hypot(point[0] - x, point[1] - y), point))
        if not candidates:
            return position
        farthest_distance, farthest = max(candidates, key=lambda item: item[0])
        span = math.hypot(max(0, right - left), max(0, bottom - top))
        desired = max(max(size) * settings['min_distance_character_fraction'],
                      span * settings['min_distance_screen_fraction'])
        threshold = min(desired, farthest_distance * .8)
        eligible = [point for distance, point in candidates if distance >= threshold]
        return self.rng.choice(eligible) if eligible else farthest

    def start(self, now, position, limits, size):
        if self.active or now < self.due:
            return False
        self.destination = self._choose_destination(position, limits, size)
        settings = self.profile['relocation']
        self.fade_out_duration = self.rng.uniform(*settings['fade_out_seconds'])
        self.fade_in_duration = self.rng.uniform(*settings['fade_in_seconds'])
        self.phase_started = now
        self.state = self.FADING_OUT
        return True

    def step(self, now):
        """Return (opacity, destination, completed); relocation gets its own zero-alpha tick."""
        if self.state == self.RESTING:
            return 1.0, None, False
        if self.state == self.FADING_OUT:
            t = (now - self.phase_started) / self.fade_out_duration
            if t >= 1:
                self.state = self.RELOCATING
                return 0.0, None, False
            return 1.0 - smooth(t), None, False
        if self.state == self.RELOCATING:
            destination = self.destination
            self.state = self.FADING_IN
            self.phase_started = now
            return 0.0, destination, False
        t = (now - self.phase_started) / self.fade_in_duration
        if t >= 1:
            self.pause(now)
            return 1.0, None, True
        return smooth(t), None, False


class IdleScheduler:
    """Schedules ambient expressions and one weighted major idle action."""
    def __init__(self, settings, now, rng=None):
        self.settings = settings
        self.rng = rng or random.Random()
        self.last_interaction = now
        self.last_ambient_index = None
        self.next_major = 0.0
        self.next_ambient = 0.0
        self.interact(now)

    def _delay(self, values):
        return self.rng.uniform(*values)

    def _choose(self, candidates, previous=None):
        available = [item for item in candidates if item.get('asset') != previous]
        if not available:
            available = candidates
        return self.rng.choices(available, weights=[item.get('weight', 1) for item in available], k=1)[0]

    def interact(self, now):
        self.last_interaction = now
        self.next_major = now + self._delay(self.settings['major']['delay_seconds'])
        self.next_ambient = now + self._delay(self.settings['ambient']['interval_seconds'])

    def finished(self, kind, now):
        if kind == 'ambient':
            self.next_ambient = now + self._delay(self.settings['ambient']['interval_seconds'])
        elif kind == 'negative':
            self.next_major = now + self._delay(self.settings['major']['delay_seconds'])

    def due(self, now, blocked=False, active_kind=None):
        """Return ('sleep', None), ('negative', payload), or ('ambient', payload)."""
        if blocked:
            return None
        if active_kind != 'negative' and now >= self.next_major:
            major = self.settings['major']
            if not major['actions']:
                self.next_major = float('inf')
                return None
            action = self.rng.choices(major['actions'],
                                      weights=[item.get('weight', 1) for item in major['actions']], k=1)[0]
            self.next_major = float('inf')
            if action['kind'] == 'sleep':
                return 'sleep', None
            if action['kind'] == 'negative':
                negative = self.settings['negative']
                selected = self._choose(negative['expressions'])
                return 'negative', {'expression': selected['asset']}
        if active_kind is not None:
            return None
        ambient = self.settings['ambient']
        if now >= self.next_ambient:
            if not ambient['expressions']:
                self.next_ambient = float('inf')
                return None
            if self.rng.random() <= ambient['probability']:
                selected = self._choose(ambient['expressions'], self.last_ambient_index)
                self.last_ambient_index = selected['asset']
                self.next_ambient = float('inf')
                return 'ambient', {'expression': selected['asset'],
                                   'hold': self._delay(ambient['hold_seconds'])}
            self.next_ambient = now + self._delay(ambient['interval_seconds'])
        return None


class StrokeDetector:
    def __init__(self, settings):
        self.settings = settings
        self.cooldown_until = 0.0
        self.reset()

    def reset(self):
        self.last = None
        self.extreme = None
        self.direction = 0
        self.turns = deque()
        self.stroke_start = None
        self.last_motion = None
        self.inside = False
        self.active = False

    def block_for(self, now, seconds):
        self.reset()
        self.cooldown_until = max(self.cooldown_until, now + seconds)

    def expire(self, now):
        if self.last_motion is not None and now - self.last_motion > self.settings['inactivity_seconds']:
            self.reset()

    def sample(self, x, y, now, head, blocked=False):
        left, bottom, right, top = head
        if blocked or not (left <= x <= right and bottom <= y <= top):
            self.reset()
            return False
        self.inside = True
        if self.last is None:
            self.last = (x, y, now)
            self.extreme = x
            self.stroke_start = x
            return False
        px, py, previous = self.last
        dt = now - previous
        dx = x - px
        width = right - left
        # Windows events may arrive in batches or share a timestamp. Do not erase
        # a valid gesture or replace its anchor just because dt is tiny/zero.
        if dt < self.settings['sample_interval_seconds']:
            return False
        if dt > self.settings['inactivity_seconds']:
            self.reset()
            return self.sample(x, y, now, head)
        if abs(dx) < width * self.settings['noise_fraction']:
            return False
        speed = abs(dx) / (dt * width)
        if speed < self.settings['min_speed_widths_per_second']:
            # A smooth reversal necessarily slows through zero. Keep the last
            # significant anchor until inactivity expiry, rather than cancelling.
            return False
        if speed > self.settings['max_speed_widths_per_second']:
            if abs(dx) > width * self.settings['jump_fraction']:
                self.reset()
                return self.sample(x, y, now, head)
            return False
        self.last = (x, y, now)
        self.last_motion = now
        self.active = True
        threshold = width * self.settings['stroke_fraction']
        if not self.direction:
            if abs(x - self.extreme) >= threshold:
                self.direction = 1 if x > self.extreme else -1
                self.extreme = x
        elif (x - self.extreme) * self.direction >= 0:
            self.extreme = x
        elif abs(x - self.extreme) >= threshold:
            length = abs(self.extreme - self.stroke_start)
            self.stroke_start = self.extreme
            self.direction *= -1
            self.extreme = x
            self.turns.append((now, length))
        while self.turns and now - self.turns[0][0] > self.settings['window_seconds']:
            self.turns.popleft()
        if now < self.cooldown_until:
            self.turns.clear()
            return False
        if (len(self.turns) >= self.settings['reversals'] and
                sum(length for _, length in self.turns) >= width * self.settings['min_distance_fraction']):
            self.turns.clear()
            self.cooldown_until = now + self.settings['cooldown_seconds']
            return True
        return False
