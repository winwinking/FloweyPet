# Acceptance-test baseline

- Base commit: 35f4f60 (tag v3.1.1), branch feature/click-through
- Run: `QT_QPA_PLATFORM=offscreen python <test>.py` from the repo root, Python 3.9, Windows
- test_chase_acceptance.py also run with `FLOWEYPET_REMINDER_LOG=<tmp>`; it waits in real time and takes ~3.6 min (a 170 s timeout kills it)
- Real FloweyPet.exe was running during the run (offscreen, separate APP_DIR)
- Per-test console output was saved to baseline/*.log (git-ignored by `*.log`)

| Test | [PASS] lines | Result |
|---|---|---|
| test_anticheat_acceptance | 50 | all passed |
| test_chase_acceptance | 36 | all passed |
| test_dialogue_arbiter_acceptance | 33 | all passed |
| test_dialogue_box_hover | 9 | all passed |
| test_flirt_hover_escape | 26 | all passed |
| test_game_detector_acceptance | 37 | all passed |
| test_game_egg_display | 12 | all passed |
| test_lock_and_layout_acceptance | 45 | all passed |
| test_lock_toggle_acceptance | 15 | all passed |

Total: 9/9 green, 0 failures, 0 tracebacks.
