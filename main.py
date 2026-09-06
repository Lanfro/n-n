"""Entry point for the Cat Agent Instagram content pipeline.

Pipeline: input_media -> vision analysis -> persona caption generation ->
human approval -> (dry-run or real) publishing via Meta Graph API.

Usage:
    python main.py --account cat_1 --media data/input_media/photo.jpg
    python main.py --account cat_2 --media data/input_media/photo.jpg --dry-run
"""

import argparse
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.approval.telegram_gateway import TelegramGateway
from src.database.db_manager import DBManager
from src.engine.prompt_generator import PersonaStore, PromptGenerator
from src.publisher.meta_publisher import MetaPublisher
from src.vault.media_host import build_media_host
from src.vault.media_vault import MediaNotSupportedError, MediaVault
from src.vault.telegram_archive import TelegramVault, VaultArchiveError
from src.vision.visual_analyzer import (
    OllamaUnavailableError,
    VisualAnalyzer,
)

DESCRIBE_PROMPT = (
    "Describe the scene in this photo in at most three concise sentences: "
    "the subjects, their expression and pose, and the setting."
)

IDENTIFY_PROMPT = (
    "Identify the cat(s) in this photo. Is it (a) a solid black cat with a "
    "small tooth sticking out of the corner of its mouth (Nero), (b) a "
    "gray-and-white cat with blue eyes and an always-grumpy-looking face "
    "(Nuvola), (c) both cats together, or (d) neither/unclear (a close-up, "
    "an object, a non-cat, or the cats are indistinguishable)? Reply with "
    "EXACTLY one word: nero, nuvola, both, or unclear."
)

# Which cat(s) may appear per account key (see --label-vault).
ACCOUNT_SUBJECTS = {
    "cat_1": {"nero", "both"},
    "cat_2": {"nuvola", "both"},
}

_SUBJECT_NERO_RE = re.compile(
    r"\bblack (cat|kitten)\b|\bsolid black\b",
    re.IGNORECASE,
)
_SUBJECT_NUVOLA_RE = re.compile(
    r"\b(?:gray|grey)[ -]?(?:and[ -]?white|white|tabby|fur)\b"
    r"|\b(?:gray|grey) and white\b"
    r"|\blight[- ]?(?:colou?red|gray|grey)\b(?: cat| kitten| tabby| fur)?"
    r"|\bwhite and (?:gray|grey)\b"
    r"|\bblue eyes?\b",
    re.IGNORECASE,
)


def _classify_subject_heuristic(description: str | None) -> str:
    """Heuristically map a stored description to nero/nuvola/both/unclear."""
    d = description or ""
    nero = bool(_SUBJECT_NERO_RE.search(d))
    nuvola = bool(_SUBJECT_NUVOLA_RE.search(d))
    if nero and nuvola:
        return "both"
    if nero:
        return "nero"
    if nuvola:
        return "nuvola"
    return "unclear"


def _parse_vision_label(response: str) -> str | None:
    """Pick the one-word subject label out of a vision answer."""
    text = (response or "").lower()
    words = {w for w in re.findall(r"\b(nero|nuvola|both|unclear)\b", text)}
    if "nero" in words and "nuvola" in words:
        return "both"
    if "nero" in words:
        return "nero"
    if "nuvola" in words:
        return "nuvola"
    if "unclear" in words:
        return "unclear"
    return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def _merge_deep(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_deep(out[key], value)
        else:
            out[key] = value
    return out


def load_config(config_path: str) -> dict:
    base_path = Path(config_path)
    local_path = base_path.with_name(
        f"{base_path.stem}.local{base_path.suffix}"
    )
    with open(base_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as fh:
            local = yaml.safe_load(fh) or {}
        cfg = _merge_deep(cfg, local)
        logger.info("Applied overrides from %s", local_path)
    return cfg


def _vault_channel(db: DBManager, vault_cfg: dict) -> TelegramVault | None:
    """Build the TelegramVault client when the channel is configured."""
    telegram_cfg = vault_cfg.get("telegram", {}) or {}
    bot_token = telegram_cfg.get("bot_token", "")
    chat_id = telegram_cfg.get("chat_id", "")
    if not bot_token or not chat_id:
        return None
    return TelegramVault(db, bot_token, chat_id)


def _archive_vault_asset(
    db: DBManager, vault_cfg: dict, vault_media_id: int, *, dry_run: bool
) -> None:
    """Best-effort archive of a vault asset to the Telegram channel."""
    if dry_run:
        return
    channel = _vault_channel(db, vault_cfg)
    if channel is None:
        logger.info("Vault channel not configured; skipping archive upload")
        return
    required = bool((vault_cfg.get("telegram", {}) or {}).get("required"))
    try:
        channel.archive(vault_media_id)
    except VaultArchiveError as exc:
        if required:
            raise
        logger.warning("Archive upload failed (best-effort): %s", exc)


def run_vault_sync(config: dict) -> int:
    """Sync manually added channel pictures into the local vault."""
    pipeline_cfg = config.get("pipeline", {})
    vault_cfg = config.get("vault", {})
    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        vault = MediaVault(db, vault_cfg.get("root", "data/vault"))
        channel = _vault_channel(db, vault_cfg)
        if channel is None:
            logger.error(
                "Vault channel not configured: set vault.telegram.bot_token "
                "and vault.telegram.chat_id."
            )
            return 2
        result = channel.sync_from_channel(vault)
        logger.info(
            "Vault sync complete: %d new item(s), offset %d",
            result["new"],
            result["offset"],
        )
        return 0
    except VaultArchiveError as exc:
        logger.error("Vault sync failed: %s", exc)
        return 1
    finally:
        db.close()


def _write_descriptions_report(
    report_path: Path, model: str, rows: list[tuple[dict, str | None, str | None]]
) -> Path:
    """Write the human-reviewable AI descriptions digest."""
    lines = [
        f"# Vault AI Descriptions ({model})",
        f"Generated {datetime.now(UTC).isoformat()}",
        "",
    ]
    for asset, description, error in rows:
        lines.append(f"## Asset #{asset['id']} — {asset['original_filename']}")
        if error:
            lines.append(f"_{error}_")
        else:
            lines.append(description)
        lines.append(f"`{asset['stored_path']}`")
        lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _write_drafts_report(
    report_path: Path,
    persona_name: str,
    rows: list[tuple[dict, dict | None, str | None]],
) -> Path:
    """Write the human-reviewable per-persona draft digest."""
    lines = [
        f"# Vault AI Drafts ({persona_name})",
        f"Generated {datetime.now(UTC).isoformat()}",
        "",
    ]
    for asset, draft, error in rows:
        lines.append(f"## Asset #{asset['id']} — {asset['original_filename']}")
        if error:
            lines.append(f"_{error}_")
        elif draft:
            hashtags = draft.get("hashtags") or []
            lines.append(f"**reel_text:** {draft.get('reel_text', '')}")
            lines.append(f"**caption:** {draft.get('caption', '')}")
            lines.append(
                "**hashtags:** " + " ".join(f"#{h}" for h in hashtags)
            )
        lines.append(f"`{asset['stored_path']}`")
        lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def run_drafts_vault(config: dict, account_key: str) -> int:
    """Batch persona-driven drafts for vault image assets.

    Uses the stored `vault_analysis` descriptions as visual context and runs
    the pipeline text model to produce a {reel_text, caption, hashtags} draft
    for the given persona over every described asset that has no draft yet.
    Skips videos and assets without a stored description. Persists each draft
    in `vault_drafts` and writes a `data/drafts_<account>.md` digest.
    Idempotent: re-running only covers assets still missing a draft. A failed
    or empty generation is retried once per asset, then the asset is skipped
    (recorded in the report) so one slow call does not abort the batch.
    """
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    persona_cfg = config.get("personas", {})
    vision_model = ollama_cfg.get("vision_model", "qwen3-vl:8b")
    text_model = ollama_cfg.get("text_model", "qwen2.5")
    report_path = Path(
        pipeline_cfg.get("drafts_report", f"data/drafts_{account_key}.md")
    )

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        assets = db.list_vault_media("image")
        if not assets:
            logger.info("No image assets in the vault; nothing to draft")
            return 0
        personas = PersonaStore(
            persona_cfg.get("config_path", "config/personas.json")
        )
        persona = personas.get(account_key)
        missing = [
            a
            for a in assets
            if db.get_vault_analysis(a["id"], vision_model) is not None
            and db.get_vault_draft(a["id"], persona["name"]) is None
        ]
        allowed = ACCOUNT_SUBJECTS.get(account_key)

        def subject_label(a: dict) -> str | None:
            row = db.get_vault_subject(a["id"])
            return row["label"] if row else None

        if allowed:
            def is_match(a: dict) -> bool:
                s = subject_label(a)
                return s is None or s in allowed  # unlabeled = legacy allow

            mismatched = [a for a in missing if not is_match(a)]
            missing = [a for a in missing if is_match(a)]
            for a in mismatched:
                logger.info(
                    "Skipping asset #%d (%s): not a %s subject (%s)",
                    a["id"],
                    a["original_filename"],
                    persona["name"],
                    subject_label(a) or "unlabeled",
                )
        if not missing:
            logger.info(
                "No image assets without a %s draft remain "
                "(all described assets are already drafted)",
                persona["name"],
            )
            return 0

        generator = PromptGenerator(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=text_model,
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            keep_alive=ollama_cfg.get("keep_alive", "30m"),
            num_predict=ollama_cfg.get("num_predict", 512),
        )
        topics = persona.get("topics") or []
        logger.info(
            "Generating %d draft(s) with %s for persona '%s' (sequential)",
            len(missing),
            text_model,
            persona["name"],
        )
        rows: list[tuple[dict, dict | None, str | None]] = []

        def draft_with_retry(
            description: str, topic: str | None = None
        ) -> tuple[dict, str | None]:
            """Generate once, retry once on failure or an unusable result."""
            kwargs = {"topic": topic} if topic else {}

            def attempt() -> dict:
                draft = generator.generate(persona, description, **kwargs)
                if PromptGenerator.is_usable(
                    draft.get("reel_text", ""), draft.get("caption", "")
                ):
                    return draft
                raise OllamaUnavailableError("empty or unusable draft returned")

            try:
                return attempt(), None
            except OllamaUnavailableError as exc:
                logger.warning(
                    "Draft generation failed, retrying once: %s", exc
                )
                try:
                    return attempt(), None
                except OllamaUnavailableError as exc2:
                    return {}, str(exc2)

        for i, asset in enumerate(missing, start=1):
            analysis = db.get_vault_analysis(asset["id"], vision_model)
            if analysis is None:
                rows.append(
                    (asset, None, "no stored description to draft from")
                )
                continue
            description = analysis["description"]
            topic = topics[asset["id"] % len(topics)] if topics else None
            try:
                draft, draft_error = draft_with_retry(description, topic)
            except Exception as exc:  # noqa: BLE001 - one bad asset not fatal
                logger.warning(
                    "Draft failed for asset #%d (%s): %s",
                    asset["id"],
                    asset["original_filename"],
                    exc,
                )
                rows.append((asset, None, f"draft failed: {exc}"))
                continue
            if draft_error is not None:
                logger.warning(
                    "Skipping asset #%d (%s): %s",
                    asset["id"],
                    asset["original_filename"],
                    draft_error,
                )
                rows.append((asset, None, f"draft failed: {draft_error}"))
                continue
            db.upsert_vault_draft(
                vault_media_id=asset["id"],
                persona_name=persona["name"],
                model=text_model,
                reel_text=draft.get("reel_text", ""),
                caption=draft.get("caption", ""),
                hashtags=",".join(draft.get("hashtags") or []),
            )
            rows.append((asset, draft, None))
            try:
                print(
                    f"[{i}/{len(missing)}] asset #{asset['id']} "
                    f"({asset['original_filename']}): "
                    f"{draft['caption'][:140]}"
                )
            except UnicodeEncodeError:  # non-ASCII caption on a cp1252 console
                print(f"[{i}/{len(missing)}] asset #{asset['id']} drafted")

        _write_drafts_report(report_path, persona["name"], rows)
        logger.info("Wrote %d draft(s) to %s", len(rows), report_path)
        return 0
    finally:
        db.close()


def run_describe_vault(config: dict) -> int:
    """Batch vision descriptions for vault image assets (T034).

    Runs the same vision model as the pipeline over every image asset that has
    no stored description yet (skips videos), persists each description in
    `vault_analysis`, and writes a `data/descriptions.md` digest for review.
    Idempotent: re-running only covers assets still missing a description. A
    failed vision call is retried once per asset, then the asset is skipped
    (recorded as an error in the report) so one slow image does not abort the
    rest of the batch.
    """
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    model = ollama_cfg.get("vision_model", "qwen3-vl:8b")
    report_path = Path(
        pipeline_cfg.get("descriptions_report", "data/descriptions.md")
    )

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        assets = db.list_vault_media("image")
        if not assets:
            logger.info("No image assets in the vault; nothing to describe")
            return 0
        missing = [
            a
            for a in assets
            if db.get_vault_analysis(a["id"], model) is None
        ]
        if not missing:
            logger.info(
                "All %d image assets already have %s descriptions",
                len(assets),
                model,
            )
            return 0

        analyzer = VisualAnalyzer(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=model,
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            num_predict=ollama_cfg.get("num_predict", 512),
            max_side=ollama_cfg.get("max_side", 1280),
            keep_alive=ollama_cfg.get("keep_alive", "30m"),
        )
        logger.info(
            "Describing %d image asset(s) with %s (sequential)",
            len(missing),
            model,
        )
        rows: list[tuple[dict, str | None, str | None]] = []

        def describe_with_retry(asset_path: Path):
            """Analyze once, retry once on failure or an empty response."""
            try:
                text = analyzer.analyze(asset_path, DESCRIBE_PROMPT)
                if text:
                    return text, None
                raise OllamaUnavailableError("empty description returned")
            except OllamaUnavailableError as exc:
                logger.warning(
                    "Vision call failed for %s, retrying once: %s",
                    asset_path.name,
                    exc,
                )
                try:
                    text = analyzer.analyze(asset_path, DESCRIBE_PROMPT)
                except OllamaUnavailableError as exc2:
                    return None, str(exc2)
                if not text:
                    return None, "empty description returned twice"
                return text, None

        for i, asset in enumerate(missing, start=1):
            path = Path(asset["stored_path"])
            if not path.exists():
                logger.warning(
                    "Asset #%d stored file missing: %s", asset["id"], path
                )
                rows.append((asset, None, "file missing"))
                continue
            try:
                description, describe_error = describe_with_retry(path)
            except Exception as exc:  # noqa: BLE001 - one bad image not fatal
                logger.warning(
                    "Failed to describe asset #%d (%s): %s",
                    asset["id"],
                    path.name,
                    exc,
                )
                rows.append((asset, None, f"analysis failed: {exc}"))
                continue
            if describe_error is not None:
                logger.warning(
                    "Skipping asset #%d (%s): %s",
                    asset["id"],
                    path.name,
                    describe_error,
                )
                rows.append((asset, None, f"analysis failed: {describe_error}"))
                continue
            db.upsert_vault_analysis(
                vault_media_id=asset["id"],
                model=model,
                prompt=DESCRIBE_PROMPT,
                description=description,
            )
            rows.append((asset, description, None))
            try:
                print(
                    f"[{i}/{len(missing)}] asset #{asset['id']} "
                    f"{path.name}: {description[:160]}"
                )
            except UnicodeEncodeError:  # non-ASCII text on a cp1252 console
                print(f"[{i}/{len(missing)}] asset #{asset['id']} described")

        _write_descriptions_report(report_path, model, rows)
        logger.info(
            "Wrote %d description(s) to %s", len(rows), report_path
        )
        return 0
    finally:
        db.close()


def run_label_vault(config: dict) -> int:
    """Derive which cat(s) each vault photo shows (nero/nuvola/both/unclear).

    First pass uses the stored vision descriptions heuristically (colours:
    black cat -> Nero, gray/white/blue-eyed -> Nuvola). Photos that stay
    `unclear` get a targeted vision call with an identity prompt; anything
    still unresolved stays `unclear`. Persists each label in `vault_subjects`
    (with the derivation method) and writes a `data/labels.md` digest.
    """
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    model = ollama_cfg.get("vision_model", "qwen3-vl:8b")
    report_path = Path(pipeline_cfg.get("labels_report", "data/labels.md"))

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        assets = db.list_vault_media("image")
        if not assets:
            logger.info("No image assets in the vault; nothing to label")
            return 0

        analyzer = VisualAnalyzer(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=model,
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            num_predict=ollama_cfg.get("num_predict", 512),
            max_side=ollama_cfg.get("max_side", 1280),
            keep_alive=ollama_cfg.get("keep_alive", "30m"),
        )
        rows: list[tuple[dict, str, str, str, str | None]] = []
        unresolved = 0
        vision_total = 0

        for i, asset in enumerate(assets, start=1):
            description = db.get_latest_vision_for_vault(asset["id"])
            label = _classify_subject_heuristic(description)
            method = "heuristic"
            error = None
            response = None
            if label == "unclear":
                path = Path(asset["stored_path"])
                if not path.exists():
                    error = f"file missing ({path.name})"
                    unresolved += 1
                else:
                    vision_total += 1
                    try:
                        response = analyzer.analyze(path, IDENTIFY_PROMPT)
                    except OllamaUnavailableError as exc:
                        error = f"vision failed: {exc}"
                        unresolved += 1
                    else:
                        vlabel = _parse_vision_label(response)
                        if vlabel:
                            label, method = vlabel, "vision"
                        else:
                            error = "vision did not return a label"
                            unresolved += 1
            db.upsert_vault_subject(asset["id"], label, method=method)
            rows.append((asset, label, method, response, error))
            try:
                print(
                    f"[{i}/{len(assets)}] asset #{asset['id']} "
                    f"{Path(asset['stored_path']).name}: {label} ({method})"
                    + (f" [{error}]" if error else "")
                )
            except UnicodeEncodeError:
                print(f"[{i}/{len(assets)}] asset #{asset['id']} -> {label}")

        lines = [
            "# Vault Subject Labels (Nero / Nuvola)",
            f"Generated {datetime.now(UTC).isoformat()}",
            "",
        ]
        counts: dict[str, int] = {}
        for asset, label, method, response, error in rows:
            counts[label] = counts.get(label, 0) + 1
            lines.append(
                f"## Asset #{asset['id']} — {asset['original_filename']}"
            )
            lines.append(f"label: **{label}** (via {method})")
            if error:
                lines.append(f"_{error}_")
            lines.append("")
        lines.append("---")
        lines.append("## Summary")
        lines.append("")
        for label in ("nero", "nuvola", "both", "unclear"):
            lines.append(f"- **{label}:** {counts.get(label, 0)}")
        lines.append("")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(
            "Labeled %d asset(s) (%d unresolved); %d targeted vision call(s); "
            "digest at %s",
            len(rows),
            unresolved,
            vision_total,
            report_path,
        )
        return 0
    finally:
        db.close()


def run_resolve_chat(config: dict, bot_token: str | None = None) -> int:
    """Resolve the vault channel's numeric chat id and print it."""
    vault_cfg = config.get("vault", {})
    telegram_cfg = vault_cfg.get("telegram", {}) or {}
    token = bot_token or telegram_cfg.get("bot_token", "")
    if not token:
        logger.error(
            "No vault bot token. Pass --vault-bot-token or set "
            "vault.telegram.bot_token."
        )
        return 2
    try:
        chat_id = TelegramVault.resolve_channel_id(token)
    except VaultArchiveError as exc:
        logger.error("%s", exc)
        return 1
    print(f"CHAT_ID={chat_id}")
    return 0


def _caption_from_draft(draft: dict) -> str:
    """Assemble the final caption from a generator draft (hashtags appended)."""
    caption = draft.get("caption", "")
    hashtags = draft.get("hashtags") or []
    if hashtags:
        caption = f"{caption}\n\n" + " ".join(f"#{h}" for h in hashtags)
    return caption


def _caption_from_post(post: dict) -> str:
    """Rebuild the final caption from a post's stored draft fields."""
    caption = post.get("caption") or ""
    hashtags = [h for h in (post.get("hashtags") or "").split(",") if h]
    if hashtags:
        caption = f"{caption}\n\n" + " ".join(f"#{h}" for h in hashtags)
    return caption


def _approve_and_publish(
    db: DBManager,
    config: dict,
    post_id: int,
    media_path: str,
    caption: str,
    *,
    dry_run: bool,
) -> int:
    """Shared approval + publishing tail for new and resumed posts.

    Moves the post through AWAITING_APPROVAL -> APPROVED -> PUBLISHED,
    applying the HITL alert (sound + Telegram push) before waiting. dry_run
    auto-approves and uses a dry publisher for hermetic testing.
    """
    meta_cfg = config.get("meta", {})
    telegram_cfg = config.get("telegram", {})
    approval_cfg = config.get("approval", {})

    if not db.transition(post_id, "AWAITING_APPROVAL"):
        logger.error("Post %d could not enter AWAITING_APPROVAL", post_id)
        return 1

    gateway = TelegramGateway(
        db=db,
        bot_token=telegram_cfg.get("bot_token", ""),
        allowed_chat_ids=telegram_cfg.get("allowed_chat_ids", []),
        decision_timeout_seconds=telegram_cfg.get(
            "decision_timeout_seconds", 1800
        ),
        sound_enabled=approval_cfg.get("sound", True),
        sound_file=approval_cfg.get("sound_file", ""),
        notify_telegram=approval_cfg.get("notify_telegram", True),
        notify_chat_id=(
            telegram_cfg.get("notify_chat_id")
            or (approval_cfg.get("notify_chat_id") or None)
        ),
    )
    if dry_run:
        # auto_approve performs the AWAITING_APPROVAL -> APPROVED transition.
        decision = gateway.auto_approve(post_id)
    else:
        decision = gateway.request_approval(post_id, media_path, caption)
    logger.info("Approval decision: %s", decision)

    if decision.get("action") == "retry":
        logger.info("Regeneration requested; pipeline would re-run.")
        db.transition(post_id, "AWAITING_APPROVAL")
        return 0
    if decision.get("action") == "discard":
        logger.info("Post discarded by human.")
        db.transition(post_id, "REJECTED")
        return 0
    if decision.get("action") == "timeout":
        logger.warning("Approval timed out; leaving post pending.")
        return 0

    if not dry_run and not db.transition(post_id, "APPROVED"):
        logger.error("Post %d could not enter APPROVED", post_id)
        return 1

    publisher = MetaPublisher(
        access_token=meta_cfg.get("access_token", ""),
        instagram_user_id=meta_cfg.get("instagram_user_id", ""),
        api_version=meta_cfg.get("api_version", "v21.0"),
        graph_base_url=meta_cfg.get(
            "graph_base_url", "https://graph.facebook.com"
        ),
        dry_run=meta_cfg.get("dry_run", True),
    )

    if publisher.dry_run:
        logger.info(
            "Meta publisher in DRY-RUN mode; skipping live container creation."
        )
        container_id = f"container_dry_{post_id}"
        media_id = f"media_dry_{post_id}"
    else:
        vault_cfg = config.get("vault", {})
        host = build_media_host(vault_cfg)
        public_url = None
        if host.is_configured():
            public_url = host.upload(media_path)
            logger.info("Media hosted at %s", public_url)
        container_id = publisher.create_media_container(
            media_path, caption, image_url=public_url
        )
        db.set_publishing_result(post_id, ig_container_id=container_id)
        logger.info("Container created: %s", container_id)

        media_id = publisher.publish_container(container_id)
        db.set_publishing_result(post_id, ig_media_id=media_id)
        logger.info("Published media id: %s", media_id)

    if not db.transition(post_id, "PUBLISHED"):
        logger.error("Post %d could not enter PUBLISHED", post_id)
        return 1
    logger.info("Pipeline complete for post %d", post_id)
    return 0


def run_pipeline(
    config: dict,
    account_key: str,
    media_path: str,
    *,
    dry_run: bool = True,
    media_source: str = "drop",
) -> int:
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    meta_cfg = config.get("meta", {})
    persona_cfg = config.get("personas", {})
    vault_cfg = config.get("vault", {})

    if dry_run:
        meta_cfg["dry_run"] = True
    else:
        # The user must explicitly opt out of dry-run for real publishing
        meta_cfg.setdefault("dry_run", False)

    ml = Path(media_path)
    if not ml.exists():
        logger.error("Media file not found: %s", ml)
        return 2

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        media_vault = None
        canonical_path = ml
        if vault_cfg:
            media_vault = MediaVault(db, vault_cfg.get("root", "data/vault"))
            if not dry_run:
                channel = _vault_channel(db, vault_cfg)
                if channel is not None:
                    sync_result = channel.sync_from_channel(media_vault)
                    logger.info("Vault auto-sync: %s", sync_result)
            try:
                vault_media_id = media_vault.ingest(ml, source=media_source)
            except MediaNotSupportedError as exc:
                logger.error("Media rejected by vault: %s", exc)
                return 2
            canonical_path = media_vault.path_of(vault_media_id)

        post_id = db.create_post(
            account_key=account_key, media_path=str(canonical_path)
        )
        if media_vault is not None:
            db.set_post_vault_media(post_id, vault_media_id)
            _archive_vault_asset(
                db, vault_cfg, vault_media_id, dry_run=dry_run
            )

        description = None
        if media_vault is not None:
            description = db.get_latest_vision_for_vault(vault_media_id)
        if description:
            logger.info(
                "Reusing stored vision analysis for vault asset #%d",
                vault_media_id,
            )
        else:
            analyzer = VisualAnalyzer(
                base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
                model=ollama_cfg.get("vision_model", "qwen3-vl:8b"),
                timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            )
            logger.info(
                "Analyzing %s with %s ...", canonical_path, analyzer.model
            )
            description = analyzer.analyze(str(canonical_path))
        db.update_content(post_id, vision_description=description)

        personas = PersonaStore(
            persona_cfg.get("config_path", "config/personas.json")
        )
        persona = personas.get(account_key)

        generator = PromptGenerator(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=ollama_cfg.get("text_model", "qwen2.5"),
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            keep_alive=ollama_cfg.get("keep_alive", "30m"),
            num_predict=ollama_cfg.get("num_predict", 512),
        )
        logger.info("Generating content for persona '%s' ...", persona["name"])
        generated = generator.generate(persona, description)
        db.update_content(
            post_id,
            persona_name=persona["name"],
            reel_text=generated["reel_text"],
            caption=generated["caption"],
            hashtags=",".join(generated["hashtags"]),
        )

        caption = _caption_from_draft(generated)
        return _approve_and_publish(
            db, config, post_id, str(canonical_path), caption,
            dry_run=dry_run,
        )

    except OllamaUnavailableError as exc:
        logger.error("Ollama unavailable: %s", exc)
        db.set_publishing_result(post_id, meta_error=str(exc))
        db.transition(post_id, "FAILED")
        return 1
    except Exception as exc:
        logger.exception("Pipeline failed")
        try:
            db.set_publishing_result(post_id, meta_error=str(exc))
            db.transition(post_id, "FAILED")
        except Exception as cleanup_exc:  # noqa: BLE001
            logger.warning("Failed to record pipeline failure: %s", cleanup_exc)
        return 1
    finally:
        db.close()


def run_retry(config: dict, post_id: int, *, dry_run: bool = True) -> int:
    """Resume a FAILED post without redoing completed work (FR-010/SC-006).

    Reuses the post row and its vault asset: vision analysis and the stored
    draft are kept when present, so only the missing stages run again.
    """
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    persona_cfg = config.get("personas", {})

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        post = db.get_post(post_id)
        if post is None:
            logger.error("Post %d not found", post_id)
            return 2
        if post["status"] != "FAILED":
            logger.error(
                "Post %d is %s; only FAILED posts can be retried",
                post_id,
                post["status"],
            )
            return 2
        if not db.transition(post_id, "PENDING_ANALYSIS"):
            logger.error(
                "Post %d could not resume from %s", post_id, post["status"]
            )
            return 1

        media_path = post["media_path"]
        if not media_path or not Path(media_path).exists():
            logger.error("Media for post %d missing: %s", post_id, media_path)
            return 2

        if dry_run:
            description = post.get("vision_description") or (
                f"[DRY-RUN] Mocked vision for post {post_id}"
            )
        elif post.get("vision_description"):
            description = post["vision_description"]
        else:
            analyzer = VisualAnalyzer(
                base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
                model=ollama_cfg.get("vision_model", "qwen3-vl:8b"),
                timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            )
            logger.info(
                "Analyzing %s with %s ...", media_path, analyzer.model
            )
            description = analyzer.analyze(media_path)
            db.update_content(post_id, vision_description=description)

        if post.get("caption"):
            caption = _caption_from_post(post)
        elif dry_run:
            caption = f"Mocked retry caption for post {post_id}"
            db.update_content(
                post_id, reel_text="Mocked reel hook", caption=caption
            )
        else:
            personas = PersonaStore(
                persona_cfg.get("config_path", "config/personas.json")
            )
            persona = personas.get(post["account_key"])
            generator = PromptGenerator(
                base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
                model=ollama_cfg.get("text_model", "qwen2.5"),
                timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
                keep_alive=ollama_cfg.get("keep_alive", "30m"),
            num_predict=ollama_cfg.get("num_predict", 512),
            )
            logger.info(
                "Generating content for persona '%s' ...", persona["name"]
            )
            generated = generator.generate(persona, description)
            db.update_content(
                post_id,
                persona_name=persona["name"],
                reel_text=generated["reel_text"],
                caption=generated["caption"],
                hashtags=",".join(generated["hashtags"]),
            )
            caption = _caption_from_draft(generated)

        return _approve_and_publish(
            db, config, post_id, media_path, caption, dry_run=dry_run
        )
    except OllamaUnavailableError as exc:
        logger.error("Ollama unavailable: %s", exc)
        db.set_publishing_result(post_id, meta_error=str(exc))
        db.transition(post_id, "FAILED")
        return 1
    except Exception as exc:
        logger.exception("Retry failed")
        try:
            db.set_publishing_result(post_id, meta_error=str(exc))
            db.transition(post_id, "FAILED")
        except Exception as cleanup_exc:  # noqa: BLE001
            logger.warning(
                "Failed to record retry failure: %s", cleanup_exc
            )
        return 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exotic Shorthair Instagram content pipeline"
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the pipeline config file",
    )
    parser.add_argument(
        "--account",
        choices=["cat_1", "cat_2"],
        required=False,
        help="Which cat persona to use",
    )
    parser.add_argument(
        "--media",
        required=False,
        help="Path to the photo/video to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prevent any real network calls to Ollama/Meta/Telegram "
        "(auto-approves) for safe smoke testing",
    )
    parser.add_argument(
        "--sync-vault",
        action="store_true",
        help="Pull operator-added pictures from the vault channel into the "
        "local vault and exit",
    )
    parser.add_argument(
        "--describe-vault",
        action="store_true",
        help="Batch-run vision descriptions (qwen3-vl) over every vault image "
        "asset that lacks one and write data/descriptions.md, then exit",
    )
    parser.add_argument(
        "--drafts-vault",
        action="store_true",
        help="Batch-run persona drafts (reel hook + caption + hashtags) over "
        "every described vault image asset that has no draft yet and write "
        "data/drafts_<account>.md, then exit; requires --account",
    )
    parser.add_argument(
        "--label-vault",
        action="store_true",
        help="Derive subject labels (nero/nuvola/both/unclear) for all vault "
        "image assets and write data/labels.md, then exit",
    )
    parser.add_argument(
        "--resolve-chat",
        action="store_true",
        help="Resolve the vault channel's numeric chat id and print it",
    )
    parser.add_argument(
        "--vault-bot-token",
        default=None,
        help="Override the vault bot token used by --resolve-chat",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Resume a FAILED post (--post-id) without repeating completed work",
    )
    parser.add_argument(
        "--post-id",
        type=int,
        default=None,
        help="Post id required by --retry",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if args.sync_vault:
        return run_vault_sync(cfg)
    if args.describe_vault:
        return run_describe_vault(cfg)
    if args.label_vault:
        return run_label_vault(cfg)
    if args.drafts_vault:
        if not args.account:
            parser.error("--drafts-vault requires --account")
            return 2
        return run_drafts_vault(cfg, args.account)
    if args.resolve_chat:
        return run_resolve_chat(cfg, bot_token=args.vault_bot_token)
    if args.retry:
        if not args.post_id:
            parser.error("--retry requires --post-id")
            return 2
        return run_retry(cfg, args.post_id, dry_run=args.dry_run)

    if not args.account or not args.media:
        parser.error("--account and --media are required for a pipeline run")
        return 2

    if args.dry_run:
        # In dry-run, skip live Ollama/Telegram entirely to keep the smoke
        # test hermetic. We still exercise DB transitions and generation.
        return run_dry_run_pipeline(cfg, args.account, args.media)

    if cfg.get("meta", {}).get("dry_run", True) and not args.dry_run:
        logger.warning(
            "config.yaml still has meta.dry_run=true; this run will not "
            "publish anything to Instagram."
        )
    return run_pipeline(cfg, args.account, args.media, dry_run=args.dry_run)


def run_dry_run_pipeline(
    config: dict, account_key: str, media_path: str
) -> int:
    """Hermetic smoke test: no network calls.

    Uses canned vision/caption text, auto-approves, and marks the post
    PUBLISHED (without contacting Ollama, Telegram, or Meta).
    """
    pipeline_cfg = config.get("pipeline", {})
    persona_cfg = config.get("personas", {})

    ml = Path(media_path)
    if not ml.exists():
        logger.error("Media file not found: %s", ml)
        return 2

    db = DBManager(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        media_vault = None
        canonical_path = ml
        vault_cfg = config.get("vault", {})
        if vault_cfg:
            media_vault = MediaVault(db, vault_cfg.get("root", "data/vault"))
            try:
                vault_media_id = media_vault.ingest(ml, source="drop")
            except MediaNotSupportedError as exc:
                logger.error("Media rejected by vault: %s", exc)
                return 2
            canonical_path = media_vault.path_of(vault_media_id)

        post_id = db.create_post(
            account_key=account_key, media_path=str(canonical_path)
        )
        if media_vault is not None:
            db.set_post_vault_media(post_id, vault_media_id)

        personas = PersonaStore(
            persona_cfg.get("config_path", "config/personas.json")
        )
        persona = personas.get(account_key)

        vision = f"[DRY-RUN] Mocked vision description for {ml.name}"
        generated = {
            "reel_text": "Mocked reel hook",
            "caption": "Mocked caption for " + persona["name"],
            "hashtags": persona["base_hashtags"] + ["mock"],
        }
        db.update_content(
            post_id,
            vision_description=vision,
            persona_name=persona["name"],
            reel_text=generated["reel_text"],
            caption=generated["caption"],
            hashtags=",".join(generated["hashtags"]),
        )

        if not db.transition(post_id, "AWAITING_APPROVAL"):
            logger.error("DRY-RUN: could not enter AWAITING_APPROVAL")
            return 1

        gateway = TelegramGateway(db=db, bot_token="", allowed_chat_ids=[])
        decide = gateway.auto_approve(post_id)  # no network
        logger.info("DRY-RUN approval: %s", decide)

        if db.get_post(post_id)["status"] != "APPROVED":
            logger.error("DRY-RUN: could not transition to APPROVED")
            return 1

        publisher = MetaPublisher(
            access_token="",
            instagram_user_id="",
            dry_run=True,
        )
        container_id = publisher.create_media_container(ml, generated["caption"])
        media_id = publisher.publish_container(container_id)
        db.set_publishing_result(post_id, ig_container_id=container_id)
        db.set_publishing_result(post_id, ig_media_id=media_id)

        if not db.transition(post_id, "PUBLISHED"):
            logger.error("DRY-RUN: could not transition to PUBLISHED")
            return 1

        post = db.get_post(post_id)
        logger.info(
            "DRY-RUN pipeline OK: post %d reached %s",
            post["id"],
            post["status"],
        )
        return 0
    except Exception:
        logger.exception("DRY-RUN failed")
        try:
            db.transition(post_id, "FAILED")
        except Exception as cleanup_exc:  # noqa: BLE001
            logger.warning("Failed to mark DRY-RUN post failed: %s", cleanup_exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())