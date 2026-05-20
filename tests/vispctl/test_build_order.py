"""Tests for resolve_build_order in vispctl.build."""

from vispctl.build import resolve_build_order

# Minimal config fixtures
BUILD_CONFIGS = {
    "apache": {"context": ".", "image": "visp-apache"},
    "jupyter-session": {
        "context": "./docker/session-manager",
        "image": "visp-jupyter-session",
        "prepare_context": "container-agent",
    },
    "session-manager": {"context": "./external/session-manager", "image": "visp-session-manager"},
}

NODE_CONFIGS = {
    "container-agent": {
        "source": "./external/container-agent",
        "output": "./external/container-agent/dist",
    },
    "webclient": {
        "source": "./external/webclient",
        "output": "./external/webclient/dist",
    },
}


def test_single_image_no_deps():
    ordered, auto_added = resolve_build_order(["apache"], BUILD_CONFIGS, NODE_CONFIGS)
    assert ordered == ["apache"]
    assert auto_added == []


def test_single_node_build():
    ordered, auto_added = resolve_build_order(["webclient"], BUILD_CONFIGS, NODE_CONFIGS)
    assert ordered == ["webclient"]
    assert auto_added == []


def test_node_builds_come_before_container_builds():
    """container-agent (node) must precede jupyter-session (container)."""
    ordered, _ = resolve_build_order(["jupyter-session", "container-agent"], BUILD_CONFIGS, NODE_CONFIGS)
    assert ordered.index("container-agent") < ordered.index("jupyter-session")


def test_dependency_auto_added():
    """Requesting jupyter-session should auto-add container-agent (its prepare_context)."""
    ordered, auto_added = resolve_build_order(["jupyter-session"], BUILD_CONFIGS, NODE_CONFIGS)
    assert "container-agent" in ordered
    assert "container-agent" in auto_added
    assert ordered.index("container-agent") < ordered.index("jupyter-session")


def test_all_builds_resolved():
    all_targets = list(BUILD_CONFIGS.keys()) + list(NODE_CONFIGS.keys())
    ordered, _ = resolve_build_order(all_targets, BUILD_CONFIGS, NODE_CONFIGS)
    # Every requested target appears in result
    for t in all_targets:
        assert t in ordered
    # container-agent before jupyter-session
    assert ordered.index("container-agent") < ordered.index("jupyter-session")


def test_no_duplicates_when_dep_already_requested():
    """Explicitly requesting container-agent + jupyter-session should not duplicate container-agent."""
    ordered, auto_added = resolve_build_order(["container-agent", "jupyter-session"], BUILD_CONFIGS, NODE_CONFIGS)
    assert ordered.count("container-agent") == 1
    assert auto_added == []  # was explicitly requested, not auto-added


def test_empty_request():
    ordered, auto_added = resolve_build_order([], BUILD_CONFIGS, NODE_CONFIGS)
    assert ordered == []
    assert auto_added == []


def test_alphabetical_tie_breaking_within_node_group():
    ordered, _ = resolve_build_order(["webclient", "container-agent"], BUILD_CONFIGS, NODE_CONFIGS)
    # Both are node builds with no inter-dependency: alphabetical order
    assert ordered == ["container-agent", "webclient"]
