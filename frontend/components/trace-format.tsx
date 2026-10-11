import { statusLabel, type Attributes, type StatusCode } from "@/lib/traces";

export function StatusBadge({ status }: { status: StatusCode }) {
  return <span className={`status-badge status-${status}`}>{statusLabel(status)}</span>;
}

export function AttributeView({ attributes }: { attributes: Attributes }) {
  // React text nodes escape HTML. Never use innerHTML or turn untrusted values into links.
  return Object.keys(attributes).length ?
    <pre className="json-view">{JSON.stringify(attributes, null, 2)}</pre> :
    <p className="muted">No attributes recorded.</p>;
}
