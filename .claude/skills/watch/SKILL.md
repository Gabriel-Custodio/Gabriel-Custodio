---
name: watch
description: This skill should be used when the user asks to "analyze video visually", "watch video", "extract frames", "visual analysis of video", or needs to evaluate generated cuts, analyze reference videos, or understand visual context before editing project scripts. Use ./bin/ffmpeg.exe to extract frames and read transcription.
version: 1.0.0
---

# Visual Video Analysis Skill (watch)

## Overview

This skill provides guidance for performing visual analysis of videos in the Fábrica de Cortes project. It extracts frames using the local FFmpeg binary (`./bin/ffmpeg.exe`) and reads transcriptions to understand video context before making editing decisions.

## When to Use This Skill

Use this skill when:
- Evaluating the quality of a generated cut
- Analyzing a reference video
- Understanding visual context before editing any project script
- The user asks to "watch" or "analyze" a video visually

## Analysis Process

### 1. Extract Frames

Use the local FFmpeg to extract key frames from the video:

```bash
./bin/ffmpeg.exe -i "input_video.mp4" -vf "fps=1/5,scale=640:-1" -q:v 2 "temp_watch/frame_%04d.jpg"
```

This extracts 1 frame every 5 seconds at 640px width, saving to `temp_watch/`.

### 2. Read Transcription

Read the video's transcription (`.vtt` or `.srt` file) to understand the spoken content and align it with visual frames.

### 3. Analyze Visual Context

Review the extracted frames alongside the transcription to identify:
- **Hook (gancho)**: The opening moments that capture attention
- **Development**: The main content progression
- **Conclusion**: The resolution or call-to-action

**Never choose random timestamps.** Ensure cuts have a clear narrative arc based on the complete video context.

### 4. Cleanup (MANDATORY)

After completing the visual analysis, **always delete the `temp_watch` folder** to avoid accumulating temporary files:

```bash
rm -rf temp_watch
```

Or on Windows:
```cmd
rmdir /s /q temp_watch
```

This cleanup step is mandatory and must be performed every time the skill is used.

## Important Notes

- Always use `./bin/ffmpeg.exe` (local binary) — never system FFmpeg
- The `temp_watch` folder is created in the project root (`D:\fabrica-cortes\`)
- Frame extraction interval (fps=1/5) can be adjusted based on video length
- Always correlate visual frames with transcription timestamps
- The analysis should inform cut decisions: hook → development → conclusion

## Example Workflow

```bash
# 1. Create temp directory
mkdir temp_watch

# 2. Extract frames (adjust fps based on video duration)
./bin/ffmpeg.exe -i "video.mp4" -vf "fps=1/5,scale=640:-1" -q:v 2 "temp_watch/frame_%04d.jpg"

# 3. Read transcription
cat video.vtt

# 4. Analyze frames + transcription together
# ... (visual analysis performed by Claude)

# 5. MANDATORY: Clean up
rmdir /s /q temp_watch
```

## Integration with Project

This skill is referenced in `CLAUDE.md` under "Análise Visual de Vídeos (Projeto Todo)". All video analysis tasks in this project should follow this skill's guidance.