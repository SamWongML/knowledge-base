import { splitCitations } from "@/lib/citations";

/** Minimal mdast shapes — enough for this transform without pulling in @types/mdast. */
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, string | number> };
}

const SKIP = new Set(["link", "linkReference", "code", "inlineCode", "definition", "html"]);

function transform(node: MdNode): void {
  if (!node.children || SKIP.has(node.type)) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string" && child.value.includes("[")) {
      for (const part of splitCitations(child.value)) {
        if (part.type === "text") next.push({ type: "text", value: part.value });
        else
          next.push({
            type: "emphasis",
            data: { hName: "cite", hProperties: { dataN: part.n } },
            children: [{ type: "text", value: String(part.n) }],
          });
      }
    } else {
      transform(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** Remark plugin: turn `[n]` markers into `<cite data-n="n">` elements. */
export function remarkCitations() {
  return (tree: MdNode) => transform(tree);
}
