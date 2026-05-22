/**
 * Markdown — renders AI/agent output as formatted markdown on the dark
 * theme. Used everywhere AI text reaches the user: council agent messages,
 * the Academic Review verdict and peer reviews, and the Explainer panel.
 *
 * Without it, agent output (which is markdown) renders as raw text and
 * lists / emphasis show as literal `-`, `*`, `**` characters — which
 * reads as unfinished to an investor or faculty audience.
 */
import { Children, isValidElement } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import { renderWithMacroCitations } from './MacroCitation'

interface MarkdownProps {
  content: string
  /** Extra classes merged onto the wrapper (e.g. a different body colour). */
  className?: string
}

/**
 * Walks every child node and replaces any [Macro: <category>] tag
 * within text segments with a MacroCitationBadge. Non-text children
 * (already-rendered React elements from nested markdown) pass through
 * untouched. Recursive so a [Macro: ...] tag inside a <strong> or an
 * <em> still renders as a badge.
 *
 * Called from every text-containing renderer below — p, li, td, th,
 * strong, em — so the badges land regardless of where the agent
 * placed the citation in its markdown.
 */
function withMacroCitations(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === 'string') {
      const parts = renderWithMacroCitations(child)
      return parts.length === 1 ? parts[0] : parts
    }
    if (isValidElement(child)
        && (child.props as { children?: ReactNode } | null)?.children) {
      const props = child.props as { children: ReactNode }
      // Recurse into the element's children — strong / em wrap an
      // inline citation in agent prose more often than not.
      return {
        ...child,
        props: { ...props, children: withMacroCitations(props.children) },
      }
    }
    return child
  })
}

export default function Markdown({ content, className = '' }: MarkdownProps) {
  return (
    <div className={`markdown-body text-sm text-slate-300 leading-relaxed space-y-2 ${className}`}>
      <ReactMarkdown
        components={{
          p: ({ children }) => <p>{withMacroCitations(children)}</p>,
          h1: ({ children }) => (
            <h3 className="text-white font-semibold text-sm mt-3">{children}</h3>
          ),
          h2: ({ children }) => (
            <h3 className="text-white font-semibold text-sm mt-3">{children}</h3>
          ),
          h3: ({ children }) => (
            <h4 className="text-white font-semibold text-sm mt-2">{children}</h4>
          ),
          h4: ({ children }) => (
            <h4 className="text-white font-semibold text-sm mt-2">{children}</h4>
          ),
          strong: ({ children }) => (
            <strong className="text-white font-semibold">
              {withMacroCitations(children)}
            </strong>
          ),
          em: ({ children }) => (
            <em className="italic">{withMacroCitations(children)}</em>
          ),
          ul: ({ children }) => (
            <ul className="list-disc pl-5 space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 space-y-1">{children}</ol>
          ),
          li: ({ children }) => <li>{withMacroCitations(children)}</li>,
          // Inline code keeps its pill styling. Block code (inside a
          // <pre>) needs to WRAP rather than scroll horizontally so the
          // Summary Statistics explainer drawer doesn't grow a
          // horizontal scrollbar on long figure tables. The `inline`
          // prop is supplied by react-markdown v9 — true for `code`,
          // false for fenced ``` blocks.
          code: ({ children, ...props }) => {
            const inline = (props as { inline?: boolean }).inline
            if (inline) {
              return (
                <code className="font-mono text-xs bg-navy-700 text-electric
                                 px-1 py-0.5 rounded
                                 break-words [overflow-wrap:anywhere]">
                  {children}
                </code>
              )
            }
            // Block code: pre-wrap forces wrapping at whitespace AND at
            // word boundaries via overflow-wrap-anywhere. Without
            // pre-wrap, the <pre>'s default `white-space: pre` keeps
            // long lines on one line and the drawer scrolls
            // horizontally — UAT feedback flagged this on the Summary
            // Statistics explainer (the agent outputs aligned figure
            // tables that exceed the panel width).
            return (
              <code className="block font-mono text-xs bg-navy-700
                               text-electric px-2 py-1 rounded
                               whitespace-pre-wrap
                               break-words [overflow-wrap:anywhere]">
                {children}
              </code>
            )
          },
          // `pre` is the outer container of a fenced code block. The
          // block-code rules above handle wrapping inside; this just
          // strips the default browser `<pre>` styling so the inner
          // <code> takes over.
          pre: ({ children }) => (
            <pre className="my-2 max-w-full
                            whitespace-pre-wrap
                            break-words [overflow-wrap:anywhere]">
              {children}
            </pre>
          ),
          // GFM tables (if remark-gfm is added later) — wrap in a
          // horizontally-scrollable container so the table's
          // intrinsic min-width doesn't force the parent drawer to
          // scroll. The wrap keeps the prose around the table
          // wrapping normally; ONLY the table is allowed to scroll.
          table: ({ children }) => (
            <div className="my-2 max-w-full overflow-x-auto">
              <table className="text-xs">{children}</table>
            </div>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-electric hover:underline"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-border pl-3 text-muted italic">
              {children}
            </blockquote>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
