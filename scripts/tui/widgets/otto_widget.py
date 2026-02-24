"""
Otto the Octopus - Animated Textual Widget for Octobatch

A procedural animation of Otto the octopus mascot moving colored blocks
between tentacle tips, with side arms, body sway, bubbles, and facial
expressions.

Usage:
    from otto_widget import OttoWidget

    otto = OttoWidget()
    otto.start_transfer(from_tip=1, to_tip=5, color="bright_red")

Standalone demo:
    python otto_demo.py
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

TICK_RATE = 1 / 8  # seconds per animation tick (8 fps)

# ─── Layout coordinates ────────────────────────────────────────────────────
# The face row is composited: LEFT_ARM + ( + INNER + ) + RIGHT_ARM
# Inner content (eyes/mouth) stays at a fixed absolute position.
# Brackets and arms shift with body sway.

INNER_START = 8        # absolute position of inner face content
LB_BASE = 7            # ( position at sway=0
RB_BASE = 17           # ) position at sway=0
HEAD_BASE = 9          # dome start position at sway=0
TENT_BASE = 6          # tentacle row start at sway=0
RENDER_WIDTH = 28      # total rendering width

# ─── Dome ──────────────────────────────────────────────────────────────────

DOME = ".─────."

# ─── Tentacles ─────────────────────────────────────────────────────────────

OTTO_ARMS_REST = "╭╯╰╮╭╯╰╮╭╯╰╮"

TIP_TO_ARM_RANGE = {
    1: (0, 1),
    2: (2, 3),
    3: (4, 5),
    4: (6, 7),
    5: (8, 9),
    6: (10, 11),
}

BLOCK_LOW = "▄"
BLOCK_FULL = "■"
BLOCK_HIGH = "▀"

# ─── Inner face expressions ───────────────────────────────────────────────
# These are the 9-char content between ( and ). Eyes stay at fixed position.

INNER_NORMAL =     "  ◕ ‿ ◕  "
INNER_FOCUS =      "  ◕ _ ◕  "
INNER_BLINK =      "  ─ ‿ ─  "
INNER_LOOK_LEFT =  "◕ ‿ ◕    "
INNER_LOOK_RIGHT = "    ◕ ‿ ◕"
INNER_HAPPY =      "  ◕ ◡ ◕  "
INNER_SURPRISE =   "  ◉ ○ ◉  "
INNER_SLEEPY =     "  ◡ ‿ ◡  "
INNER_DEAD =       "  ✗ _ ✗  "

IDLE_INNER_FACES = [
    INNER_BLINK,
    INNER_LOOK_LEFT,
    INNER_LOOK_RIGHT,
    INNER_HAPPY,
]

# Face expression timing (in ticks at 8fps)
FACE_MIN_INTERVAL = 16   # ~2s between expressions
FACE_MAX_INTERVAL = 40   # ~5s between expressions
FACE_MIN_DURATION = 4    # ~500ms expression duration
FACE_MAX_DURATION = 6    # ~750ms expression duration

# ─── Body sway ─────────────────────────────────────────────────────────────

SWAY_MIN_INTERVAL = 24   # ~3s
SWAY_MAX_INTERVAL = 48   # ~6s

# ─── Sleepy ────────────────────────────────────────────────────────────────

IDLE_SLEEPY_TICKS = 240   # ~30s

# ─── Tentacle behaviors ───────────────────────────────────────────────────
# Arm/body animations separate from facial expressions.

def _make_wave_left() -> list[dict]:
    return [
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "─╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "╯ ╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "─╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "╯ ╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "─╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
    ]

def _make_wave_right() -> list[dict]:
    return [
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰─"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰ "},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰─"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰ "},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰─"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
    ]

def _make_wiggle() -> list[dict]:
    return [
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
        {"arms": "╯╰╮╭╯╰╮╭╯╰╮╭"},
        {"arms": "╰╮╭╯╰╮╭╯╰╮╭╯"},
        {"arms": "╮╭╯╰╮╭╯╰╮╭╯╰"},
        {"arms": "╭╯╰╮╭╯╰╮╭╯╰╮"},
    ]

TENT_BEHAVIORS: list[tuple[callable, int]] = [
    (_make_wave_left, 2),
    (_make_wave_right, 2),
    (_make_wiggle, 3),
]

TENT_MIN_INTERVAL = 40   # ~5s
TENT_MAX_INTERVAL = 80   # ~10s

# ─── Side arms ─────────────────────────────────────────────────────────────
# Each side arm is a string extending outward from the bracket.
# Left arm reads left-to-right: tip is leftmost char.
# Right arm reads left-to-right: tip is rightmost char.

SIDE_ARM_REST_LEN = 2

# Side arm animation types
class SideArmAnim(Enum):
    IDLE = "idle"
    EXTEND_WIGGLE = "extend_wiggle"    # extend, wiggle travels back, retract
    TIP_FLICK = "tip_flick"            # brief tip curl up then back
    FLAG_WAVE = "flag_wave"            # extend and wave flag
    PUFF = "puff"                      # brackets go < > briefly

# Timing
SIDE_ARM_MIN_INTERVAL = 32   # ~4s
SIDE_ARM_MAX_INTERVAL = 72   # ~9s
PUFF_MIN_INTERVAL = 80       # ~10s
PUFF_MAX_INTERVAL = 200      # ~25s

# ─── Bubbles ──────────────────────────────────────────────────────────────

BUBBLE_CHARS = ["◦", "○", "°", "∘"]
BUBBLE_MIN_INTERVAL = 16    # ~2s
BUBBLE_MAX_INTERVAL = 48    # ~6s
BUBBLE_RISE_SPEED = 3       # ticks per row of upward movement
BUBBLE_ROWS = 6             # rows above Otto's head
BUBBLE_MIN_COL = 3
BUBBLE_MAX_COL = 22


# ═══════════════════════════════════════════════════════════════════════════
# Block phase
# ═══════════════════════════════════════════════════════════════════════════

class BlockPhase(Enum):
    LOW = 0
    FULL = 1
    HIGH = 2

    @property
    def display_char(self) -> str:
        return [BLOCK_LOW, BLOCK_FULL, BLOCK_HIGH][self.value]


# ═══════════════════════════════════════════════════════════════════════════
# Tentacle Transfer
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TentacleTransfer:
    """A block being moved through Otto's tentacles from one tip to another."""
    from_tip: int
    to_tip: int
    color: str
    _arm_path: list[int] = field(default_factory=list, repr=False)
    _path_index: int = 0
    _phase: BlockPhase = BlockPhase.LOW
    done: bool = False

    def __post_init__(self) -> None:
        start_lo, start_hi = TIP_TO_ARM_RANGE[self.from_tip]
        end_lo, end_hi = TIP_TO_ARM_RANGE[self.to_tip]

        if self.from_tip < self.to_tip:
            self._arm_path = list(range(start_lo, end_hi + 1))
        else:
            self._arm_path = list(range(start_hi, end_lo - 1, -1))

        self._path_index = 0
        self._set_initial_phase()

    def _is_rising_arm(self, arm_idx: int) -> bool:
        return arm_idx % 4 < 2

    def _set_initial_phase(self) -> None:
        if not self._arm_path:
            self.done = True
            return
        self._phase = BlockPhase.LOW if self._is_rising_arm(self._arm_path[0]) else BlockPhase.HIGH

    def tick(self) -> None:
        if self.done:
            return
        current_arm = self._arm_path[self._path_index]
        rising = self._is_rising_arm(current_arm)
        if rising:
            if self._phase == BlockPhase.LOW:
                self._phase = BlockPhase.FULL
            elif self._phase == BlockPhase.FULL:
                self._phase = BlockPhase.HIGH
            else:
                self._advance()
        else:
            if self._phase == BlockPhase.HIGH:
                self._phase = BlockPhase.FULL
            elif self._phase == BlockPhase.FULL:
                self._phase = BlockPhase.LOW
            else:
                self._advance()

    def _advance(self) -> None:
        self._path_index += 1
        if self._path_index >= len(self._arm_path):
            self.done = True
            return
        next_arm = self._arm_path[self._path_index]
        self._phase = BlockPhase.LOW if self._is_rising_arm(next_arm) else BlockPhase.HIGH

    @property
    def current_arm_index(self) -> int | None:
        if self.done or self._path_index >= len(self._arm_path):
            return None
        return self._arm_path[self._path_index]

    @property
    def display_char(self) -> str:
        return self._phase.display_char


# ═══════════════════════════════════════════════════════════════════════════
# Pool Block
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PoolBlock:
    color: str
    char: str = "■"


# ═══════════════════════════════════════════════════════════════════════════
# Bubble
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Bubble:
    row: int       # 0=just above head, increases upward
    col: int       # horizontal position
    char: str
    age: int = 0

    def tick(self) -> bool:
        self.age += 1
        if self.age % BUBBLE_RISE_SPEED == 0:
            self.row += 1
            self.col += random.choice([-1, 0, 0, 1])
            self.col = max(0, min(RENDER_WIDTH - 1, self.col))
        return self.row < BUBBLE_ROWS


# ═══════════════════════════════════════════════════════════════════════════
# Side Arm State
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SideArmState:
    """State for one side arm (left or right)."""
    is_left: bool
    length: int = SIDE_ARM_REST_LEN
    tip_char: str = "─"      # tip character (─, ╰/╯, ╭/╮, ~)
    wiggle_pos: int = -1     # -1 = no wiggle, 0+ = position from tip
    flag: bool = False

    # Animation playback
    _anim_frames: list[dict] | None = None
    _frame_idx: int = 0

    def render(self) -> str:
        """Render this arm as a string.

        Left arm: tip is leftmost, body-end is rightmost.
        Right arm: body-end is leftmost, tip is rightmost.
        """
        # Build shaft
        shaft = list("─" * self.length)

        # Apply wiggle
        if 0 <= self.wiggle_pos < len(shaft):
            if self.is_left:
                shaft[self.wiggle_pos] = "~"
            else:
                shaft[-(self.wiggle_pos + 1)] = "~"

        # Apply tip
        if self.is_left:
            shaft[0] = self.tip_char
        else:
            shaft[-1] = self.tip_char

        # Apply flag
        result = "".join(shaft)
        if self.flag:
            if self.is_left:
                result = "⚑" + result
            else:
                result = result + "⚑"

        return result

    def start_anim(self, anim_type: SideArmAnim) -> None:
        """Start a side arm animation sequence."""
        if anim_type == SideArmAnim.EXTEND_WIGGLE:
            self._anim_frames = self._build_extend_wiggle()
        elif anim_type == SideArmAnim.TIP_FLICK:
            self._anim_frames = self._build_tip_flick()
        elif anim_type == SideArmAnim.FLAG_WAVE:
            self._anim_frames = self._build_flag_wave()
        else:
            self._anim_frames = None

        self._frame_idx = 0

    def tick(self) -> None:
        """Advance animation one frame."""
        if self._anim_frames is None:
            return

        if self._frame_idx < len(self._anim_frames):
            frame = self._anim_frames[self._frame_idx]
            self.length = frame.get("length", SIDE_ARM_REST_LEN)
            self.tip_char = frame.get("tip", "─")
            self.wiggle_pos = frame.get("wiggle", -1)
            self.flag = frame.get("flag", False)
            self._frame_idx += 1
        else:
            # Animation done — reset to rest
            self.length = SIDE_ARM_REST_LEN
            self.tip_char = "─"
            self.wiggle_pos = -1
            self.flag = False
            self._anim_frames = None
            self._frame_idx = 0

    @property
    def is_animating(self) -> bool:
        return self._anim_frames is not None

    # ─── Animation builders ──────────────────────────────────────────

    def _tip_up(self) -> str:
        return "╰" if self.is_left else "╯"

    def _tip_down(self) -> str:
        return "╭" if self.is_left else "╮"

    def _build_extend_wiggle(self) -> list[dict]:
        """Extend arm, wiggle travels from tip to body, retract."""
        frames = []
        # Extend
        for l in range(SIDE_ARM_REST_LEN, 6):
            frames.append({"length": l})
        # Hold extended
        frames.append({"length": 5})
        # Wiggle travels from tip (0) to body (4)
        for wp in range(5):
            frames.append({"length": 5, "wiggle": wp})
        # Retract
        for l in range(5, SIDE_ARM_REST_LEN - 1, -1):
            frames.append({"length": l})
        return frames

    def _build_tip_flick(self) -> list[dict]:
        """Quick tip curl up then back."""
        tip = self._tip_up()
        return [
            {"tip": tip},
            {"tip": tip},
            {"tip": tip},
            {"tip": "─"},
        ]

    def _build_flag_wave(self) -> list[dict]:
        """Extend and wave a flag."""
        tip_up = self._tip_up()
        frames = []
        # Extend
        for l in range(SIDE_ARM_REST_LEN, 5):
            frames.append({"length": l})
        # Wave flag (alternate tip up/down)
        for _ in range(3):
            frames.append({"length": 4, "tip": tip_up, "flag": True})
            frames.append({"length": 4, "tip": tip_up, "flag": True})
            frames.append({"length": 4, "tip": "─", "flag": True})
            frames.append({"length": 4, "tip": "─", "flag": True})
        # Retract
        for l in range(4, SIDE_ARM_REST_LEN - 1, -1):
            frames.append({"length": l})
        return frames


# ═══════════════════════════════════════════════════════════════════════════
# Otto State
# ═══════════════════════════════════════════════════════════════════════════

class OttoState:
    """Animation state for Otto. Pure logic, no Textual dependency.

    Layout (top to bottom):
        6 bubble rows (highest first)
        1 head row (dome)
        1 face row (side arms + brackets + eyes)
        1 tentacle row
        1 pool row
    """

    MAX_POOL_PER_TIP = 3

    def __init__(self) -> None:
        self.transfers: list[TentacleTransfer] = []
        self.pool: dict[int, list[PoolBlock]] = {tip: [] for tip in range(1, 7)}
        self.tick_count: int = 0

        # Persistent mood face (set by orchestrator for terminal states)
        self.mood_face: str | None = None

        # Face expression
        self._inner_face: str = INNER_NORMAL
        self._face_override: str | None = None
        self._face_ticks_remaining: int = 0
        self._next_face_at: int = random.randint(FACE_MIN_INTERVAL, FACE_MAX_INTERVAL)
        self._idle_ticks: int = 0
        self._sleepy: bool = False

        # Reactive face
        self._reactive_face: str | None = None
        self._reactive_ticks: int = 0

        # Body sway
        self._sway_offset: int = 0
        self._next_sway_at: int = random.randint(SWAY_MIN_INTERVAL, SWAY_MAX_INTERVAL)

        # Bracket override (for puff animation)
        self._bracket: str = "()"
        self._puff_ticks: int = 0
        self._next_puff_at: int = random.randint(PUFF_MIN_INTERVAL, PUFF_MAX_INTERVAL)

        # Tentacle behaviors
        self._tent_behavior: list[dict] | None = None
        self._tent_frame: int = 0
        self._next_tent_at: int = random.randint(TENT_MIN_INTERVAL, TENT_MAX_INTERVAL)

        # Side arms
        self._left_arm = SideArmState(is_left=True)
        self._right_arm = SideArmState(is_left=False)
        self._next_side_arm_at: int = random.randint(SIDE_ARM_MIN_INTERVAL, SIDE_ARM_MAX_INTERVAL)

        # Burst tracking
        self._peak_concurrent: int = 0

        # Bubbles
        self._bubbles: list[Bubble] = []
        self._next_bubble_at: int = random.randint(BUBBLE_MIN_INTERVAL, BUBBLE_MAX_INTERVAL)

    # ─── Public API ─────────────────────────────────────────────────

    def start_transfer(self, from_tip: int, to_tip: int, color: str) -> None:
        if not (1 <= from_tip <= 6 and 1 <= to_tip <= 6):
            raise ValueError(f"Tips must be 1-6, got {from_tip} and {to_tip}")
        if from_tip == to_tip:
            raise ValueError(f"from_tip and to_tip must differ")

        self.transfers.append(TentacleTransfer(from_tip=from_tip, to_tip=to_tip, color=color))

        # Wake up
        self._idle_ticks = 0
        self._sleepy = False
        self._face_override = None
        self._face_ticks_remaining = 0

        # Track peak
        self._peak_concurrent = max(self._peak_concurrent, len(self.transfers))

        # Surprise
        if len(self.transfers) >= 3 and self._reactive_face is None:
            self._reactive_face = INNER_SURPRISE
            self._reactive_ticks = 3

    def trigger_flag(self) -> None:
        """Wave a checkered flag on a random arm (call when a run completes)."""
        arm = random.choice([self._left_arm, self._right_arm])
        if not arm.is_animating:
            arm.start_anim(SideArmAnim.FLAG_WAVE)
            self._reactive_face = INNER_HAPPY
            self._reactive_ticks = 6

    # ─── Tick ───────────────────────────────────────────────────────

    def tick(self) -> None:
        self.tick_count += 1

        # ── Transfers ───────────────────────────────────────────
        for t in self.transfers:
            t.tick()
        for t in self.transfers:
            if t.done:
                pool = self.pool[t.to_tip]
                pool.append(PoolBlock(color=t.color))
                while len(pool) > self.MAX_POOL_PER_TIP:
                    pool.pop(0)

        was_active = len(self.transfers) > 0
        self.transfers = [t for t in self.transfers if not t.done]
        is_active = len(self.transfers) > 0

        # Post-burst happy
        if was_active and not is_active:
            if self._peak_concurrent >= 2:
                self._reactive_face = INNER_HAPPY
                self._reactive_ticks = 4
            self._peak_concurrent = 0

        # ── Reactive face countdown ─────────────────────────────
        if self._reactive_face is not None:
            self._reactive_ticks -= 1
            if self._reactive_ticks <= 0:
                self._reactive_face = None

        # ── Idle systems ────────────────────────────────────────
        if not is_active:
            self._idle_ticks += 1

            # Face expressions
            if self._face_ticks_remaining > 0:
                self._face_ticks_remaining -= 1
                if self._face_ticks_remaining <= 0:
                    self._face_override = None
                    self._next_face_at = self._idle_ticks + random.randint(
                        FACE_MIN_INTERVAL, FACE_MAX_INTERVAL
                    )
            elif self._idle_ticks >= self._next_face_at:
                if self._idle_ticks >= IDLE_SLEEPY_TICKS:
                    self._sleepy = True
                    if random.random() < 0.03:
                        self._face_override = INNER_BLINK
                        self._face_ticks_remaining = 2
                else:
                    self._face_override = random.choice(IDLE_INNER_FACES)
                    self._face_ticks_remaining = random.randint(
                        FACE_MIN_DURATION, FACE_MAX_DURATION
                    )

            # Tentacle behaviors
            if self._tent_behavior is not None:
                self._tent_frame += 1
                if self._tent_frame >= len(self._tent_behavior):
                    self._tent_behavior = None
                    self._tent_frame = 0
                    self._next_tent_at = self._idle_ticks + random.randint(
                        TENT_MIN_INTERVAL, TENT_MAX_INTERVAL
                    )
            elif self._idle_ticks >= self._next_tent_at and not self._sleepy:
                behaviors, weights = zip(*TENT_BEHAVIORS)
                chosen = random.choices(behaviors, weights=weights)[0]
                self._tent_behavior = chosen()
                self._tent_frame = 0

            # Side arm animations
            if not self._left_arm.is_animating and not self._right_arm.is_animating:
                self._next_side_arm_at -= 1
                if self._next_side_arm_at <= 0 and not self._sleepy:
                    arm = random.choice([self._left_arm, self._right_arm])
                    anim = random.choices(
                        [SideArmAnim.EXTEND_WIGGLE, SideArmAnim.TIP_FLICK],
                        weights=[2, 5],
                    )[0]
                    arm.start_anim(anim)
                    self._next_side_arm_at = random.randint(
                        SIDE_ARM_MIN_INTERVAL, SIDE_ARM_MAX_INTERVAL
                    )
        else:
            self._idle_ticks = 0

        # ── Side arm tick (always, for ongoing animations) ──────
        self._left_arm.tick()
        self._right_arm.tick()

        # ── Body sway (always) ──────────────────────────────────
        self._next_sway_at -= 1
        if self._next_sway_at <= 0:
            if self._sway_offset == 0:
                self._sway_offset = random.choice([-1, 1])
            else:
                self._sway_offset = 0
            self._next_sway_at = random.randint(SWAY_MIN_INTERVAL, SWAY_MAX_INTERVAL)

        # ── Bracket puff (always) ───────────────────────────────
        if self._puff_ticks > 0:
            self._puff_ticks -= 1
            if self._puff_ticks <= 0:
                self._bracket = "()"
        else:
            self._next_puff_at -= 1
            if self._next_puff_at <= 0:
                self._bracket = "<>"
                self._puff_ticks = random.randint(3, 5)
                self._next_puff_at = random.randint(PUFF_MIN_INTERVAL, PUFF_MAX_INTERVAL)

        # ── Bubbles ─────────────────────────────────────────────
        self._bubbles = [b for b in self._bubbles if b.tick()]
        self._next_bubble_at -= 1
        if self._next_bubble_at <= 0:
            self._bubbles.append(Bubble(
                row=0,
                col=random.randint(BUBBLE_MIN_COL, BUBBLE_MAX_COL),
                char=random.choice(BUBBLE_CHARS),
            ))
            self._next_bubble_at = random.randint(BUBBLE_MIN_INTERVAL, BUBBLE_MAX_INTERVAL)

    @property
    def is_active(self) -> bool:
        return len(self.transfers) > 0

    # ─── Face resolution ────────────────────────────────────────

    def _get_inner_face(self) -> str:
        if self.mood_face:
            return self.mood_face
        if self._reactive_face:
            return self._reactive_face
        if self.is_active:
            return INNER_FOCUS
        if self._face_override:
            return self._face_override
        if self._sleepy:
            return INNER_SLEEPY
        return INNER_NORMAL

    # ─── Rendering ──────────────────────────────────────────────

    def render_bubble_row(self, bubble_row: int) -> Text:
        chars = [" "] * RENDER_WIDTH
        for b in self._bubbles:
            if b.row == bubble_row and 0 <= b.col < RENDER_WIDTH:
                chars[b.col] = b.char
        result = Text()
        for ch in chars:
            if ch != " ":
                result.append(ch, style="dim cyan")
            else:
                result.append(ch)
        return result

    def render_head(self) -> Text:
        return Text(" " * (HEAD_BASE + self._sway_offset) + DOME)

    def render_face(self) -> Text:
        """Render the face row: side arms + bracket + inner face + bracket + side arms."""
        inner = self._get_inner_face()
        sway = self._sway_offset
        lb = self._bracket[0]
        rb = self._bracket[1]

        lb_pos = LB_BASE + sway
        rb_pos = RB_BASE + sway

        left_str = self._left_arm.render()
        right_str = self._right_arm.render()

        left_start = lb_pos - len(left_str)
        right_start = rb_pos + 1
        right_end = right_start + len(right_str)

        width = max(right_end, INNER_START + len(inner), RENDER_WIDTH)
        chars = [" "] * width
        styles: list[str | None] = [None] * width

        # Inner face at fixed position
        for i, ch in enumerate(inner):
            pos = INNER_START + i
            if 0 <= pos < width:
                chars[pos] = ch

        # Brackets
        if 0 <= lb_pos < width:
            chars[lb_pos] = lb
        if 0 <= rb_pos < width:
            chars[rb_pos] = rb

        # Left arm
        for i, ch in enumerate(left_str):
            pos = left_start + i
            if 0 <= pos < width:
                chars[pos] = ch

        # Right arm
        for i, ch in enumerate(right_str):
            pos = right_start + i
            if 0 <= pos < width:
                chars[pos] = ch

        # Build Rich text
        result = Text()
        for ch, style in zip(chars, styles):
            result.append(ch)
        return result

    def render_tentacles(self) -> Text:
        """Render tentacle row with active transfer blocks overlaid."""
        # Get tentacle base
        tent_frame = None
        if self._tent_behavior is not None and 0 <= self._tent_frame < len(self._tent_behavior):
            tent_frame = self._tent_behavior[self._tent_frame]

        if tent_frame and tent_frame.get("arms") and not self.is_active:
            tent_str = tent_frame["arms"]
        else:
            tent_str = OTTO_ARMS_REST

        chars = list(tent_str)
        styles: list[str | None] = [None] * len(chars)

        # Overlay transfer blocks
        for transfer in self.transfers:
            arm_idx = transfer.current_arm_index
            if arm_idx is not None and 0 <= arm_idx < len(chars):
                chars[arm_idx] = transfer.display_char
                styles[arm_idx] = transfer.color

        # Build with padding to align under face
        pad = TENT_BASE + self._sway_offset
        result = Text(" " * pad)
        for ch, style in zip(chars, styles):
            if style:
                result.append(ch, style=f"bold {style}")
            else:
                result.append(ch)
        return result

    def render_pool(self) -> Text:
        pool_chars = list(" " * len(OTTO_ARMS_REST))
        pool_styles: list[str | None] = [None] * len(pool_chars)

        for tip in range(1, 7):
            blocks = self.pool[tip]
            if not blocks:
                continue
            lo, hi = TIP_TO_ARM_RANGE[tip]
            center = (lo + hi) // 2
            positions = [center]
            if len(blocks) >= 2:
                positions.append(center - 1)
            if len(blocks) >= 3:
                positions.append(center + 1)
            for i, pos in enumerate(positions):
                if 0 <= pos < len(pool_chars) and pool_chars[pos] == " ":
                    block = blocks[-(i + 1)]
                    pool_chars[pos] = block.char
                    pool_styles[pos] = block.color

        pad = TENT_BASE + self._sway_offset
        result = Text(" " * pad)
        for ch, style in zip(pool_chars, pool_styles):
            if style:
                result.append(ch, style=f"bold {style}")
            else:
                result.append(ch)
        return result


# ═══════════════════════════════════════════════════════════════════════════
# Textual Widget
# ═══════════════════════════════════════════════════════════════════════════

class OttoWidget(Widget):
    """Animated Otto the Octopus widget for Textual TUI apps.

    Layout (10 rows):
        6 bubble rows
        1 head (dome)
        1 face (side arms + brackets + eyes)
        1 tentacles
        1 pool

    Public API:
        start_transfer(from_tip, to_tip, color) — animate block between tips
        trigger_flag() — wave a checkered flag (call when a run completes)
    """

    DEFAULT_CSS = """
    OttoWidget {
        width: auto;
        height: 10;
        padding: 0 1;
    }

    OttoWidget Static {
        height: 1;
        width: auto;
    }
    """

    def __init__(self, *args, tick_rate: float = TICK_RATE, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.state = OttoState()
        self._tick_rate = tick_rate

    def compose(self) -> ComposeResult:
        for i in range(BUBBLE_ROWS - 1, -1, -1):
            yield Static(id=f"otto-bubble-{i}")
        yield Static(id="otto-head")
        yield Static(id="otto-face")
        yield Static(id="otto-tentacles")
        yield Static(id="otto-pool")

    def on_mount(self) -> None:
        self.set_interval(self._tick_rate, self._tick)
        self._update_display()

    def start_transfer(self, from_tip: int, to_tip: int, color: str) -> None:
        self.state.start_transfer(from_tip, to_tip, color)

    def trigger_flag(self) -> None:
        self.state.trigger_flag()

    def set_mood(self, face: str | None) -> None:
        """Set a persistent mood face, or None to return to normal."""
        self.state.mood_face = face

    def _tick(self) -> None:
        self.state.tick()
        self._update_display()

    def _update_display(self) -> None:
        try:
            for i in range(BUBBLE_ROWS):
                self.query_one(f"#otto-bubble-{i}", Static).update(
                    self.state.render_bubble_row(i)
                )
            self.query_one("#otto-head", Static).update(self.state.render_head())
            self.query_one("#otto-face", Static).update(self.state.render_face())
            self.query_one("#otto-tentacles", Static).update(self.state.render_tentacles())
            self.query_one("#otto-pool", Static).update(self.state.render_pool())
        except Exception:
            pass
