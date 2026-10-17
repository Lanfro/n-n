"""AI Video Reel pipeline: briefs, assisted production, validation, publishing.

Sub-modules:
    * ``brief``     - creative set + vault anchors -> self-contained ReelBrief JSON
    * ``producer``  - assisted Playwright producer on a free image-to-video service
    * ``selectors`` - external-service selectors isolated in one place
    * ``validator`` - ffprobe validation + ffmpeg repair (silent audio, upscale)
    * ``models``    - shared dataclasses for briefs, clips and session state
"""
