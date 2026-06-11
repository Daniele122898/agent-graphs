import { BaseEdge, EdgeLabelRenderer, getBezierPath, Position, useInternalNode, type EdgeProps, type InternalNode } from "@xyflow/react";

// Floating edge: instead of fixed left/right handles, the edge attaches to
// each node's border at the angle toward its peer, so layout never produces
// loops or overlaps. Reciprocal pairs (A→B and B→A) carry data.offsetDir
// (+1/-1, derived in Canvas) and are shifted perpendicular to the
// center-to-center axis so they render as two parallel arcs instead of one
// unreadable overlap.

const PAIR_OFFSET = 18;
const FALLBACK = { width: 170, height: 60 };

function center(node: InternalNode) {
  const w = node.measured.width ?? FALLBACK.width;
  const h = node.measured.height ?? FALLBACK.height;
  return { x: node.internals.positionAbsolute.x + w / 2, y: node.internals.positionAbsolute.y + h / 2, w, h };
}

// Intersection of the line between the two node centers with `node`'s border
// (the standard React Flow floating-edges construction).
function intersection(node: InternalNode, other: InternalNode) {
  const n = center(node);
  const o = center(other);
  const w = n.w / 2;
  const h = n.h / 2;
  const xx1 = (o.x - n.x) / (2 * w) - (o.y - n.y) / (2 * h);
  const yy1 = (o.x - n.x) / (2 * w) + (o.y - n.y) / (2 * h);
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1);
  const xx3 = a * xx1;
  const yy3 = a * yy1;
  return { x: w * (xx3 + yy3) + n.x, y: h * (-xx3 + yy3) + n.y };
}

function side(node: InternalNode, point: { x: number; y: number }): Position {
  const n = center(node);
  const left = n.x - n.w / 2;
  const top = n.y - n.h / 2;
  if (point.x <= left + 1) return Position.Left;
  if (point.x >= left + n.w - 1) return Position.Right;
  if (point.y <= top + 1) return Position.Top;
  return Position.Bottom;
}

export default function FloatingEdge({ id, source, target, label, selected, style, data, markerEnd }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const s = intersection(sourceNode, targetNode);
  const t = intersection(targetNode, sourceNode);

  // Perpendicular shift for reciprocal pairs.
  const dir = (data?.offsetDir as number | undefined) ?? 0;
  const dx = t.x - s.x;
  const dy = t.y - s.y;
  const len = Math.hypot(dx, dy) || 1;
  const ox = (-dy / len) * PAIR_OFFSET * dir;
  const oy = (dx / len) * PAIR_OFFSET * dir;

  const [path, labelX, labelY] = getBezierPath({
    sourceX: s.x + ox,
    sourceY: s.y + oy,
    sourcePosition: side(sourceNode, s),
    targetX: t.x + ox,
    targetY: t.y + oy,
    targetPosition: side(targetNode, t),
  });

  const stroke = selected ? "var(--primary)" : ((style?.stroke as string) ?? "#b1b1b7");
  const strokeWidth = selected ? 2 : (style?.strokeWidth as number) ?? 1.5;

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ ...style, stroke, strokeWidth }} />
      {label ? (
        <EdgeLabelRenderer>
          {/* pointerEvents none: clicks fall through to the edge's interaction
              path beneath, which is what selects the edge */}
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
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
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
