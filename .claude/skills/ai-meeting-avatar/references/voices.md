# Kokoro TTS Voice Reference

## American English (lang: en-us)

| # | Voice ID    | Description                |
|---|-------------|----------------------------|
| 1 | af_heart    | warm female (default)      |
| 2 | af_sky      | bright female              |
| 3 | af_nova     | expressive female          |
| 4 | af_sarah    | clear female               |
| 5 | am_adam     | natural male               |
| 6 | am_michael  | deep male                  |

## British English (lang: en-gb)

| # | Voice ID      | Description      |
|---|---------------|------------------|
| 7 | bf_emma       | British female   |
| 8 | bf_isabella   | British female   |
| 9 | bm_george     | British male     |
|10 | bm_lewis      | British male     |

## Notes

- American voices use `lang: en-us`, British voices use `lang: en-gb`
- The `set-voice.sh` script handles lang switching automatically
- Voice changes take effect on next `/join` (config is loaded at startup)
- No additional downloads needed to switch voices — all voices ship with the Kokoro model files
