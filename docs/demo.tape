# =============================================================================
# PatchPilot — VHS Terminal Storyboard (18-Second Demo GIF)
# =============================================================================
# Record with Charmbracelet VHS:
#   vhs docs/demo.tape
#
# Compress below 1.5 MB for fast GitHub README loading:
#   gifsicle -O3 --colors 128 --lossy=80 patchpilot-brand/banners/demo.gif -o patchpilot-brand/banners/demo-optimized.gif
#   ls -lh patchpilot-brand/banners/demo-optimized.gif
# =============================================================================

Output patchpilot-brand/banners/demo.gif

Set FontSize 16
Set Width 1100
Set Height 560
Set Theme "Catppuccin Mocha"
Set Padding 24
Set TypingSpeed 35ms
Set WindowBar Colorful

# -----------------------------------------------------------------------------
# Frame 1 (0–3s): Highlight the friction — unreviewed PR diff with a secret leak
# -----------------------------------------------------------------------------
Type "# PR #42 opened: auth.py modified — checking for bugs & leaked secrets..."
Enter
Sleep 2s

# -----------------------------------------------------------------------------
# Frame 2 (3–10s): Execute core command with clean, syntax-highlighted output
# -----------------------------------------------------------------------------
Type "uv run python examples/quickstart/demo_review.py"
Enter
Sleep 4s

# -----------------------------------------------------------------------------
# Frame 3 (10–16s): Demonstrate the transformation — Launch API & Health Check
# -----------------------------------------------------------------------------
Type "TESTING=1 uv run python -c \"from fastapi.testclient import TestClient; from app.main import app; c = TestClient(app); print(c.get('/health').json()); print('Free Models Loaded:', len(c.get('/api/free-models').json()['models']))\""
Enter
Sleep 4s

# -----------------------------------------------------------------------------
# Frame 4 (16–20s): End with the repository URL & quickstart command
# -----------------------------------------------------------------------------
Type "# Ship safer PRs with your own keys -> https://github.com/89Aman/PatchPilot | docker compose up --build -d"
Enter
Sleep 3s
