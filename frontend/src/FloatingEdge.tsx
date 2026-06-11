import { BaseEdge, EdgeLabelRenderer, useInternalNode, useReactFlow, type EdgeProps, type InternalNode } from "@xyflow/react";

// Floating edge: attaches to each node's border at the angle toward the
// edge's midpoint, so layout never produces fixed-handle loops or overlaps.
// The path is a quadratic bezier through a midpoint that can be displaced
// perpendicular to the center-to-center axis:
//  - reciprocal pairs (A→B and B→A, data.reciprocal) default to PAIR_OFFSET;
//    the perpendicular axis flips with edge direction, so the same default
//    pushes the two edges to OPPOSITE sides — do not "fix" the sign per edge
//    (a canonical source<target flip cancels the separation; learned the
//    hard way).
//  - the user can drag the bend handle at the midpoint; the displacement is
//    stored as data.curve (persisted as GraphEdge.curve) and overrides the
//    default. Dragging close to straight snaps back to 0 (= auto).

const PAIR_OFFSET = 28;
const SNAP_TO_AUTO = 6;
const FALLBACK = { width: 170, height: 60 };

function center(node: InternalNode) {
  const w = node.measured.width ?? FALLBACK.width;
  const h = node.measured.height ?? FALLBACK.height;
  return { x: node.internals.positionAbsolute.x + w / 2, y: node.internals.positionAbsolute.y + h / 2, w, h };
}

// Intersection of the line from `node`'s center toward `p` with the node's
// border (the standard React Flow floating-edges construction).
function intersectToward(node: InternalNode, p: { x: number; y: number }) {
  const n = center(node);
  const w = n.w / 2;
  const h = n.h / 2;
  const xx1 = (p.x - n.x) / (2 * w) - (p.y - n.y) / (2 * h);
  const yy1 = (p.x - n.x) / (2 * w) + (p.y - n.y) / (2 * h);
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1);
  const xx3 = a * xx1;
  const yy3 = a * yy1;
  return { x: w * (xx3 + yy3) + n.x, y: h * (-xx3 + yy3) + n.y };
}

export default function FloatingEdge({ id, source, target, label, selected, style, data, markerEnd }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  const { screenToFlowPosition } = useReactFlow();
  if (!sourceNode || !targetNode) return null;

  const sc = center(sourceNode);
  const tc = center(targetNode);
  const dx = tc.x - sc.x;
  const dy = tc.y - sc.y;
  const len = Math.hypot(dx, dy) || 1;
  const perpX = -dy / len;
  const perpY = dx / len;

  const curve = (data?.curve as number) ?? 0;
  const reciprocal = Boolean(data?.reciprocal);
  const bend = curve !== 0 ? curve : reciprocal ? PAIR_OFFSET : 0;

  const baseMidX = (sc.x + tc.x) / 2;
  const baseMidY = (sc.y + tc.y) / 2;
  const midX = baseMidX + perpX * bend;
  const midY = baseMidY + perpY * bend;

  const s = intersectToward(sourceNode, { x: midX, y: midY });
  const t = intersectToward(targetNode, { x: midX, y: midY });

  // Quadratic bezier passing THROUGH the midpoint: control = 2·mid − (s+t)/2.
  const cX = 2 * midX - (s.x + t.x) / 2;
  const cY = 2 * midY - (s.y + t.y) / 2;
  const path = `M ${s.x},${s.y} Q ${cX},${cY} ${t.x},${t.y}`;

  const stroke = selected ? "var(--primary)" : ((style?.stroke as string) ?? "#b1b1b7");
  const strokeWidth = selected ? 2 : ((style?.strokeWidth as number) ?? 1.5);

  const onCurveChange = data?.onCurveChange as ((edgeId: string, curve: number) => void) | undefined;
  const startBendDrag = (ev: React.PointerEvent) => {
    if (!onCurveChange) return;
    ev.stopPropagation();
    ev.preventDefault();
    const move = (e2: PointerEvent) => {
      const p = screenToFlowPosition({ x: e2.clientX, y: e2.clientY });
      const d = (p.x - baseMidX) * perpX + (p.y - baseMidY) * perpY;
      onCurveChange(id, Math.abs(d) < SNAP_TO_AUTO ? 0 : Math.round(d));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ ...style, stroke, strokeWidth }} />
      <EdgeLabelRenderer>
        {/* bend handle: drag perpendicular to route the edge around clutter */}
        <div
          onPointerDown={startBendDrag}
          className="nodrag nopan"
          title="Drag to bend this link"
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${midX}px, ${midY}px)`,
            width: 11,
            height: 11,
            borderRadius: "50%",
            background: selected ? "var(--primary)" : "var(--surface)",
            border: `1.5px solid ${selected ? "var(--primary)" : "#b1b1b7"}`,
            cursor: "grab",
            pointerEvents: "all",
          }}
        />
        {label ? (
          // pointerEvents none: clicks fall through to the edge's interaction
          // path beneath, which is what selects the edge
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -100%) translate(${midX}px, ${midY - 9}px)`,
              background: "var(--surface)",
              border: `1px solid ${selected ? "var(--primary)" : "var(--border)"}`,
              borderRadius: "var(--r-sm)",
              padding: "2px 7px",
              fontSize: 11,
              color: selected ? "var(--primary)" : "var(--text-muted)",
              maxWidth: 240,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              pointerEvents: "none",
            }}
          >
            {label}
          </div>
        ) : null}
      </EdgeLabelRenderer>
    </>
  );
}
