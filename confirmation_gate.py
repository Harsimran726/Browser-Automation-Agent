from __future__ import annotations

DESTRUCTIVE_KEYWORDS = [
	"submit",
	"save",
	"delete",
	"send",
	"purchase",
	"confirm",
	"pay",
	"remove",
	"place order",
	"checkout",
	"buy",
]


def is_destructive(action: dict) -> bool:
	if action.get("action") != "click":
		return False

	target = action.get("target", "").lower()
	return any(keyword in target for keyword in DESTRUCTIVE_KEYWORDS)


def confirmation_gate(action: dict) -> bool:
	if not is_destructive(action):
		return True

	print()
	print("  " + "─" * 50)
	print("  CONFIRMATION REQUIRED")
	print("  " + "─" * 50)
	print(f"  Action    : {action.get('action', '').upper()} → '{action.get('target', '')}'")
	print(f"  Reasoning : {action.get('reasoning', '')}")
	print("  Reversible: NO")
	print("  " + "─" * 50)

	answer = input("  Proceed? [y/n]: ").strip().lower()
	print()
	return answer == "y"
