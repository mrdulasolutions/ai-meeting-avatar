# AI Meeting Avatar

An installable agent skill and local runtime for a synced meeting avatar.

The product promise is simple:

- join a room directly
- listen with Whisper
- answer with Gemma or Claude
- speak with Kokoro
- drive a talking avatar through a virtual camera path

## What changed

This repo is now avatar-first instead of audio-first-with-avatar-as-a-bonus.

- `ai-avatar join <room>` connects directly to the target LiveKit room
- `ai-avatar doctor` is the source of truth for readiness
- onboarding configures brain, voice, avatar photo, and readiness against the active config file
- the Claude skill under `.claude/skills/ai-meeting-avatar/` uses the same CLI/state model as the app

## Quick start

```bash
git clone https://github.com/mrdulasolutions/ai-meeting-avatar.git
cd ai-meeting-avatar
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
ai-avatar onboard
ai-avatar doctor
ai-avatar join my-room
```

## Core commands

```bash
ai-avatar onboard
ai-avatar doctor
ai-avatar state --json-output
ai-avatar join <room>
ai-avatar brain --set claude
ai-avatar voice --set af_heart
ai-avatar avatar enable
ai-avatar avatar set-photo ~/Pictures/headshot.jpg
ai-avatar avatar test
ai-avatar avatar status
ai-avatar generate-token --room my-room --identity phone
```

## Recommended path

For most users:

1. Use `Claude` first unless you specifically need local-only Gemma.
2. Enable the avatar and set a front-facing photo.
3. Run `ai-avatar doctor` until it reports `Ready to join`.
4. Start local LiveKit with Docker or point `.env` at LiveKit Cloud.
5. Use your virtual microphone and camera devices in Meet or Zoom.

## Installable skill

The agent-installable skill lives at:

`/.claude/skills/ai-meeting-avatar/SKILL.md`

It relies on:

- `ai-avatar doctor --json-output` for readiness
- `ai-avatar voice --set ...` for voice changes
- `ai-avatar join <room>` for direct room joins

## Notes

- Avatar sync quality still depends heavily on the selected renderer and hardware.
- `ai-avatar doctor` is the fastest way to see whether the full pipeline is ready.
- `scripts/launch.sh` starts the avatar in the background after a readiness check.
