"""Observable behaviour of provider-keyed loopback tool deny policies.

One job: assert that `loopback_provider_policy.apply` correctly updates OpenClaw's
`tools.byProvider` to deny connector tool patterns for model-provider keys routed to
CLI backends (`claude-cli`, `google-antigravity-cli`), covering per-session model
overrides and fallbacks without mutating input.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import loopback_provider_policy  # noqa: E402


def test_session_override_defaults_wildcard_denies_provider() -> None:
    """An agent with DeepSeek primary but Anthropic wildcard routed to claude-cli denies anthropic."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/*": {"agentRuntime": {"id": "claude-cli"}}
                }
            },
            "entries": {
                "main": {"model": "opencode-go/deepseek-v4.1-flash"}
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library", "n8n-mcp"])
    assert result["tools"]["byProvider"]["anthropic"]["deny"] == ["n8n-mcp__*", "vault-library__*"]


def test_exact_model_pin_denies_exact_provider_model_key() -> None:
    """An exact pin on anthropic/claude-opus-5-5 denies that exact key."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}
                }
            },
            "entries": {
                "main": {"model": "opencode-go/deepseek-v4.1-flash"}
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library", "n8n-mcp"])
    assert result["tools"]["byProvider"]["anthropic/claude-opus-5-5"]["deny"] == [
        "n8n-mcp__*",
        "vault-library__*",
    ]


def test_fallback_antigravity_model_denies_antigravity_provider_with_carried_connectors() -> None:
    """A fallback naming google-antigravity-cli denies only carried connectors for that provider."""
    config = {
        "agents": {
            "entries": {
                "main": {
                    "model": {
                        "primary": "opencode-go/deepseek-v4.1-flash",
                        "fallbacks": ["google-antigravity-cli/gemini-3.8-flash"],
                    }
                }
            }
        }
    }
    result = loopback_provider_policy.apply(
        config,
        connectors=["vault-library", "n8n-mcp", "firecrawl"],
        agy_connectors=["firecrawl"],
    )
    assert result["tools"]["byProvider"]["google-antigravity-cli"]["deny"] == ["firecrawl__*"]


def test_empty_agy_connectors_writes_no_agy_key() -> None:
    """When agy carries no connectors locally, no deny key is written for google-antigravity-cli."""
    config = {
        "agents": {
            "entries": {
                "main": {
                    "model": {
                        "primary": "opencode-go/deepseek-v4.1-flash",
                        "fallbacks": ["google-antigravity-cli/gemini-3.8-flash"],
                    }
                }
            }
        }
    }
    result = loopback_provider_policy.apply(
        config,
        connectors=["vault-library", "n8n-mcp"],
        agy_connectors=[],
    )
    assert "google-antigravity-cli" not in result.get("tools", {}).get("byProvider", {})


def test_existing_byprovider_entry_preserves_profile_and_merges_deny() -> None:
    """Existing configuration options like profile and existing deny items are preserved."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/*": {"agentRuntime": {"id": "claude-cli"}}
                }
            }
        },
        "tools": {
            "byProvider": {
                "anthropic": {
                    "profile": "minimal",
                    "deny": ["x"],
                }
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library"])
    assert result["tools"]["byProvider"]["anthropic"] == {
        "profile": "minimal",
        "deny": ["vault-library__*", "x"],
    }


def test_existing_exact_byprovider_entry_receives_deny_when_provider_selected() -> None:
    """An exact byProvider entry for a selected provider also receives the deny list."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/*": {"agentRuntime": {"id": "claude-cli"}}
                }
            }
        },
        "tools": {
            "byProvider": {
                "anthropic/claude-sonnet-5-5": {
                    "profile": "minimal",
                }
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library"])
    assert result["tools"]["byProvider"]["anthropic/claude-sonnet-5-5"]["deny"] == ["vault-library__*"]
    assert result["tools"]["byProvider"]["anthropic/claude-sonnet-5-5"]["profile"] == "minimal"
    assert result["tools"]["byProvider"]["anthropic"]["deny"] == ["vault-library__*"]


def test_no_cli_runtime_returns_input_unchanged() -> None:
    """When no CLI runtime is routed anywhere, the configuration is returned unmodified."""
    config = {
        "agents": {
            "entries": {
                "main": {"model": "openai/gpt-5"}
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library"])
    assert result is config


def test_idempotence_and_no_mutation() -> None:
    """Applying the policy does not mutate the original dictionary and is idempotent."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/*": {"agentRuntime": {"id": "claude-cli"}}
                }
            }
        }
    }
    original = copy.deepcopy(config)
    first = loopback_provider_policy.apply(config, ["vault-library"])
    assert config == original
    second = loopback_provider_policy.apply(first, ["vault-library"])
    assert second == first


def test_openclaw_and_lazy_mcp_never_appear_in_deny() -> None:
    """Internal loopback and gateway server names are never added to deny lists."""
    config = {
        "agents": {
            "defaults": {
                "models": {
                    "anthropic/*": {"agentRuntime": {"id": "claude-cli"}}
                }
            }
        }
    }
    result = loopback_provider_policy.apply(
        config,
        connectors=["openclaw", "lazy-mcp", "vault-library"],
    )
    deny = result["tools"]["byProvider"]["anthropic"]["deny"]
    assert "openclaw__*" not in deny
    assert "lazy-mcp__*" not in deny
    assert deny == ["vault-library__*"]


def test_foreign_provider_with_agy_model_name_does_not_create_agy_key() -> None:
    """A model ref with agy as model name on another provider does not name the agy provider."""
    config = {
        "agents": {
            "entries": {
                "main": {
                    "model": "opencode-go/google-antigravity-cli",
                }
            }
        }
    }
    result = loopback_provider_policy.apply(
        config,
        connectors=["vault-library"],
        agy_connectors=["vault-library"],
    )
    assert "google-antigravity-cli" not in result.get("tools", {}).get("byProvider", {})


def test_agent_level_model_pin_produces_global_provider_deny_overapproximation() -> None:
    """An agent-level model pin produces a global deny rule: a session of any agent may pick that model."""
    config = {
        "agents": {
            "entries": {
                "specialist": {
                    "models": {
                        "anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}
                    }
                }
            }
        }
    }
    result = loopback_provider_policy.apply(config, ["vault-library", "n8n-mcp"])
    assert result["tools"]["byProvider"]["anthropic/claude-opus-5-5"]["deny"] == [
        "n8n-mcp__*",
        "vault-library__*",
    ]
