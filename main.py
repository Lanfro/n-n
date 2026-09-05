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


def run_pipeline(
    config: dict,
    account_key: str,
    media_path: str,
    *,
    dry_run: bool = True,
) -> int:
    pipeline_cfg = config.get("pipeline", {})
    ollama_cfg = config.get("ollama", {})
    meta_cfg = config.get("meta", {})
    telegram_cfg = config.get("telegram", {})
    persona_cfg = config.get("personas", {})

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
        post_id = db.create_post(account_key=account_key, media_path=str(ml))

        analyzer = VisualAnalyzer(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=ollama_cfg.get("vision_model", "qwen2-vl"),
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
        )
        logger.info("Analyzing %s with %s ...", ml, analyzer.model)
        description = analyzer.analyze(str(ml))
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

        if not db.transition(post_id, "AWAITING_APPROVAL"):
            logger.error("Post %d could not enter AWAITING_APPROVAL", post_id)
            return 1

        caption = generated["caption"]
        if generated["hashtags"]:
            caption = caption + "\n\n" + " ".join(
                f"#{h}" for h in generated["hashtags"]
            )

        gateway = TelegramGateway(
            db=db,
            bot_token=telegram_cfg.get("bot_token", ""),
            allowed_chat_ids=telegram_cfg.get("allowed_chat_ids", []),
            decision_timeout_seconds=telegram_cfg.get(
                "decision_timeout_seconds", 1800
            ),
        )
        decision = gateway.request_approval(post_id, str(ml), caption)
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

        # defer to config/arg dry_run handling
        if not db.transition(post_id, "APPROVED"):
            logger.error("Post %d could not enter APPROVED", post_id)
            return 1

        publisher = MetaPublisher(
            access_token=meta_cfg.get("access_token", ""),
            instagram_user_id=meta_cfg.get("instagram_user_id", ""),
            api_version=meta_cfg.get("api_version", "v21.0"),
            graph_base_url=meta_cfg.get("graph_base_url", "https://graph.facebook.com"),
            dry_run=meta_cfg.get("dry_run", True),
        )

        effective_caption = caption  # already includes hashtags

        if publisher.dry_run:
            logger.info(
                "Meta publisher in DRY-RUN mode; skipping live container creation."
            )
            container_id = f"container_dry_{post_id}"
            media_id = f"media_dry_{post_id}"
        else:
            container_id = publisher.create_media_container(ml, effective_caption)
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
        required=True,
        help="Which cat persona to use",
    )
    parser.add_argument(
        "--media",
        required=True,
        help="Path to the photo/video to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prevent any real network calls to Ollama/Meta/Telegram "
        "(auto-approves) for safe smoke testing",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

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
        post_id = db.create_post(account_key=account_key, media_path=str(ml))

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