# fsgc demo tape — render with: vhs docs/img-src/demo.tape
# Output GIF: docs/img/demo.gif

Output docs/img/demo.gif

Set Theme "Catppuccin Mocha"
Set FontSize 14
Set Width 1100
Set Height 700
Set Padding 24
Set TypingSpeed 30ms
Set PlaybackSpeed 1.0

# Stage a synthetic ~2.6 GB tree with the standard suspects (hidden).
Hide
Type "bash docs/img-src/stage-demo.sh /tmp/fsgc-demo/junk >/dev/null 2>&1 && clear"
Enter
Sleep 1500ms
Show

# Headline: run fsgc against the synthetic tree.
Type "uv run fsgc scan /tmp/fsgc-demo/junk --dry-run --no-cache --budget 3"
Sleep 600ms
Enter

# Live MCTS scanner UI streams the directory tree as it walks. Three-second
# budget means the scan settles, then the proposal renders below it.
Sleep 6s

# Land on the interactive checkbox. Toggle the first four structural groups.
Space
Sleep 350ms
Down
Sleep 200ms
Space
Sleep 350ms
Down
Sleep 200ms
Space
Sleep 350ms
Down
Sleep 200ms
Space
Sleep 600ms

# Confirm the selection. --dry-run means the sweep simulates — nothing is
# actually deleted — and prints the would-be-freed total.
Enter
Sleep 3500ms
