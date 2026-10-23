"""
backend.services.to_be_diagram - renders the To-Be state as an agentic-workflow image.

Deliberately not a generic swimlane: every node is one of AI Agent, Deterministic Automation,
Human Activity, System Interaction (tag) or Decision/Validation (HITL diamond), so the shape of
the picture matches what the To-Be table already says rather than restating a generic process map.

No structured data is invented here - every label is a string already produced by the deterministic
current-state query or the To-Be Designer's validated output.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

NODE_COLORS = {
    "ai_agent": "#3B82C4",
    "deterministic": "#2E9E5B",
    "human": "#8A8F98",
    "start_end": "#4B5563",
    "decision": "#C77D22",
}


def _group_to_be_activities(to_be_rows: List[dict]) -> List[Dict[str, Any]]:
    groups: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []
    for row in sorted(to_be_rows, key=lambda r: r["to_be_sequence"]):
        if row["change_type"] == "Eliminated":
            continue
        seq = row["to_be_sequence"]
        if seq not in groups:
            groups[seq] = {
                "to_be_sequence": seq,
                "label": row["to_be_activity"],
                "change_types": set(),
                "responsible_agents": set(),
                "systems": set(),
                "human_in_the_loop": False,
            }
            order.append(seq)
        g = groups[seq]
        g["change_types"].add(row["change_type"])
        if row.get("responsible_agent"):
            g["responsible_agents"].add(row["responsible_agent"])
        g["systems"].update(row.get("system_used") or [])
        g["human_in_the_loop"] = g["human_in_the_loop"] or bool(row.get("human_in_the_loop"))
    return [groups[seq] for seq in order]


def _node_type(group: Dict[str, Any]) -> str:
    if "AI Agent" in group["change_types"]:
        return "ai_agent"
    if "Automated (Deterministic)" in group["change_types"]:
        return "deterministic"
    return "human"


def render(to_be_rows: List[dict], out_path: str, title: str = "To-Be State") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

    groups = _group_to_be_activities(to_be_rows)

    nodes: List[Dict[str, Any]] = [{"kind": "start_end", "label": "Start", "type": "start_end"}]
    for g in groups:
        node_type = _node_type(g)
        label = g["label"]
        if node_type == "ai_agent" and g["responsible_agents"]:
            label = f"{label}\n({', '.join(sorted(g['responsible_agents']))})"
        nodes.append({"kind": "activity", "label": label, "type": node_type,
                      "systems": sorted(g["systems"])})
        if g["human_in_the_loop"]:
            nodes.append({"kind": "decision", "label": "Human-in-the-Loop Approval", "type": "decision"})
    nodes.append({"kind": "start_end", "label": "End / Next Stage Handoff", "type": "start_end"})

    n = len(nodes)
    box_w, box_h = 6.4, 0.8
    tag_w, tag_gap = 2.8, 0.2
    cx = 4.4
    row_h = 1.35
    canvas_w = cx + box_w / 2 + tag_gap + tag_w + 0.4
    fig_height = max(4.0, row_h * n * 0.85)
    fig, ax = plt.subplots(figsize=(canvas_w * 0.92, fig_height))
    ax.set_xlim(0, canvas_w)
    ax.set_ylim(0, (n + 0.6) * row_h)
    ax.axis("off")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=16, wrap=True)

    ys = [(n - i) * row_h for i in range(n)]

    for node, y in zip(nodes, ys):
        color = NODE_COLORS[node["type"]]
        if node["kind"] == "decision":
            dw, dh = box_w / 2, 0.6
            diamond = Polygon([(cx - dw, y), (cx, y + dh), (cx + dw, y), (cx, y - dh)],
                              closed=True, facecolor=color, edgecolor="white", linewidth=1.2, zorder=3)
            ax.add_patch(diamond)
        else:
            box = FancyBboxPatch((cx - box_w / 2, y - box_h / 2), box_w, box_h,
                                 boxstyle="round,pad=0.08,rounding_size=0.15",
                                 facecolor=color, edgecolor="white", linewidth=1.2, zorder=3)
            ax.add_patch(box)
        ax.text(cx, y, node["label"], ha="center", va="center", fontsize=8.5,
                color="white", fontweight="bold" if node["kind"] == "start_end" else "normal", zorder=4,
                wrap=True)
        if node.get("systems"):
            tag = " / ".join(node["systems"])
            tag_x = cx + box_w / 2 + tag_gap
            ax.add_patch(FancyBboxPatch((tag_x, y - 0.28), tag_w, 0.56,
                                        boxstyle="round,pad=0.05,rounding_size=0.12",
                                        facecolor="#E5E7EB", edgecolor="#9CA3AF", linewidth=0.8, zorder=3))
            ax.text(tag_x + tag_w / 2, y, tag, ha="center", va="center",
                    fontsize=7, color="#111827", zorder=4, wrap=True)

    half_heights = [0.6 if node["kind"] == "decision" else box_h / 2 for node in nodes]
    for (y_from, y_to, h_from, h_to) in zip(ys[:-1], ys[1:], half_heights[:-1], half_heights[1:]):
        arrow = FancyArrowPatch((cx, y_from - h_from - 0.03), (cx, y_to + h_to + 0.03),
                                arrowstyle="-|>", mutation_scale=14, color="#374151",
                                linewidth=1.4, zorder=2)
        ax.add_patch(arrow)

    legend_items = [
        ("AI Agent", NODE_COLORS["ai_agent"]),
        ("Deterministic Automation", NODE_COLORS["deterministic"]),
        ("Human Activity", NODE_COLORS["human"]),
        ("Human-in-the-Loop / Decision", NODE_COLORS["decision"]),
        ("Start / End", NODE_COLORS["start_end"]),
    ]
    for i, (text, color) in enumerate(legend_items):
        ly = 0.35
        lx = 0.3 + i * 1.9
        ax.add_patch(FancyBboxPatch((lx, ly), 0.28, 0.2, boxstyle="round,pad=0.02", facecolor=color,
                                    edgecolor="none"))
        ax.text(lx + 0.38, ly + 0.1, text, ha="left", va="center", fontsize=6.5, color="#111827")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return out_path
