"""Totoro color palette — truecolor ANSI escape sequences.

Background : #0D1117
Blue       : #111C2E / #1A5FA8 / #56A0F0 / #A8CFFA
Amber      : #1F1600 / #8A4E0A / #F5B240 / #FFD899
Copper     : #1E0E0C / #8C3020 / #E06840 / #F8BEA0
Ivory      : #1A1714 / #60503A / #D4BA8E / #F5ECD8
"""

_ESC = "\033["
RESET = f"{_ESC}0m"

# ── Blue ──
BLUE_DK   = f"{_ESC}38;2;26;95;168m"    # #1A5FA8
BLUE      = f"{_ESC}38;2;86;160;240m"    # #56A0F0  prompt / links
BLUE_LT   = f"{_ESC}38;2;168;207;250m"   # #A8CFFA  body text / cursor

# ── Amber ──
AMBER_DK  = f"{_ESC}38;2;138;78;10m"    # #8A4E0A
AMBER     = f"{_ESC}38;2;245;178;64m"    # #F5B240  logo / main accent
AMBER_LT  = f"{_ESC}38;2;255;216;153m"   # #FFD899

# ── Copper (warning / error only) ──
COPPER_DK = f"{_ESC}38;2;140;48;32m"     # #8C3020
COPPER    = f"{_ESC}38;2;224;104;64m"     # #E06840  warning / error
COPPER_LT = f"{_ESC}38;2;248;190;160m"    # #F8BEA0

# ── Ivory ──
IVORY_DK  = f"{_ESC}38;2;96;80;58m"     # #60503A  divider / dim
IVORY     = f"{_ESC}38;2;212;186;142m"   # #D4BA8E  secondary text
IVORY_LT  = f"{_ESC}38;2;245;236;216m"   # #F5ECD8

# ── Semantic aliases ──
ACCENT    = AMBER       # logo main accent
PROMPT    = BLUE        # prompt / links
BODY      = BLUE_LT     # body text
SECONDARY = IVORY       # secondary text
DIM       = IVORY_DK    # divider / dim elements
WARN      = COPPER      # warning / error
ERR       = COPPER      # error (same as warn)
BOLD      = f"{_ESC}1m"
