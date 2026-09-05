"""Entry point for the Cat Agent Instagram content pipeline.

Pipeline: input_media -> vision analysis -> persona caption generation ->
human approval -> (dry-run or real) publishing via Meta Graph API.

Usage:
    python main.py --account cat_1 --media data/input_media/photo.jpg
    python main.py --account cat_2 --media data/input_media/photo.jpg --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

from src.approval.telegram_gateway import TelegramGateway
from src.database.db_manager import DBManager
from src.engine.prompt_generator import PersonaStore, PromptGenerator
from src.publisher.meta_publisher import MetaPublisher
from src.vault.media_host import build_media_host
from src.vault.media_vault import MediaNotSupportedError, MediaVault
from src.vault.telegram_archive import TelegramVault, VaultArchiveError
from src.vision.visual_analyzer import OllamaUnavailableError, VisualAnalyzer

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
                model=ollama_cfg.get("vision_model", "qwen2-vl"),
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
                model=ollama_cfg.get("vision_model", "qwen2-vl"),
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