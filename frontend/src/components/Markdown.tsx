/**
 * Markdown — renders AI/agent output as formatted markdown on the dark
 * theme. Used everywhere AI text reaches the user: council agent messages,
 * the Academic Review verdict and peer reviews, and the Explainer panel.
 *
 * Without it, agent output (which is markdown) renders as raw text and
 * lists / emphasis show as literal `-`, `*`, `**` characters — which
 * reads as unfinished to an investor or faculty audience.
 */
import ReactMarkdown from 'react-markdown'

interface MarkdownProps {
  content: string
  /** Extra classes merged onto the wrapper (e.g. a different body colour). */
  className?: string
}

export default function Markdown({ content, className = '' }: MarkdownProps) {
  return (
    <div className={`markdown-body text-sm text-slate-300 leading-relaxed space-y-2 ${className}`}>
      <ReactMarkdown
        components={{
          p: ({ children }) => <p>{children}</p>,
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
            <strong className="text-white font-semibold">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => (
            <ul className="list-disc pl-5 space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 space-y-1">{children}</ol>
          ),
          li: ({ children }) => <li>{children}</li>,
          code: ({ children }) => (
            <code className="font-mono text-xs bg-navy-700 text-electric px-1 py-0.5 rounded">
              {children}
            </code>
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
