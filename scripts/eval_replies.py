"""Measure reply-interpretation accuracy against the real Ollama model.

Usage: uv run python scripts/eval_replies.py [path/to/replies.jsonl]
Requires Ollama running with REPLY_MODEL pulled. Not part of the unit tests.
"""

import json
import sys
from pathlib import Path

# Make the project importable when run as `python scripts/eval_replies.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.integrations.llm_client import ReplyInterpreterClient  # noqa: E402
from app.integrations.ollama import OllamaClient  # noqa: E402
from app.services.messages import load_messages  # noqa: E402

DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "replies_es.jsonl"


def main() -> int:
    """Run every labelled reply through the model and print a report."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURES
    settings = get_settings()
    client = ReplyInterpreterClient(OllamaClient(settings.ollama_url, settings.ollama_timeout_seconds), settings.reply_model)
    bot_message = load_messages(settings.messages_file).survey_templates[0].text
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    opt_out_cases = opt_out_hits = rating_cases = rating_hits = false_opt_outs = 0
    for case in cases:
        result = client.interpret(bot_message, case["reply"])
        expected_opt_out = "opt_out" in case["intents"]
        got_opt_out = "opt_out" in result.intents and result.confidence >= settings.opt_out_threshold
        opt_out_cases += expected_opt_out
        opt_out_hits += expected_opt_out and got_opt_out
        false_opt_outs += got_opt_out and not expected_opt_out
        if case["rating"]:
            rating_cases += 1
            rating_hits += result.rating == case["rating"]
        mark = "OK " if (got_opt_out == expected_opt_out and result.rating == case["rating"]) else "ERR"
        print(f"{mark} {case['reply']!r}: intents={result.intents} rating={result.rating} conf={result.confidence:.2f}")
    print(f"\nopt-out recall: {opt_out_hits}/{opt_out_cases}  (missed opt-outs are the costly error)")
    print(f"false opt-outs: {false_opt_outs}")
    print(f"rating accuracy: {rating_hits}/{rating_cases}")
    return 0 if opt_out_hits == opt_out_cases else 1


if __name__ == "__main__":
    sys.exit(main())
