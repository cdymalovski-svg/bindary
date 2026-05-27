import { Component } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RotateCcw, ArrowLeft, Copy } from 'lucide-react';
import { toast } from 'sonner';

/**
 * Catches render-time errors in the wrapped subtree and shows an actionable
 * recovery UI instead of a blank screen. The bug that prompted this:
 * a `ReferenceError` deep inside the editor caused production books to
 * open to a pure-yellow screen with no clue what failed. With this boundary
 * in place the user sees the actual error text and can either copy it for a
 * bug report, retry the render, or escape back to the library.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep the component-stack info available for the "Copy details" button.
    this.setState({ info });
    // Surface to the browser console so DevTools + remote logging pick it up.
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] caught render error:', error, info);
  }

  reset = () => this.setState({ error: null, info: null });

  copyDetails = async () => {
    const { error, info } = this.state;
    const text = [
      `Error: ${error?.message || error}`,
      '',
      'Stack:',
      error?.stack || '(no stack)',
      '',
      'Component stack:',
      info?.componentStack || '(no component stack)',
      '',
      `URL: ${window.location.href}`,
      `User agent: ${navigator.userAgent}`,
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Error details copied to clipboard');
    } catch {
      toast.error('Could not copy — select the text below manually.');
    }
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="min-h-screen bg-desk flex items-center justify-center px-6 py-10">
        <div
          className="w-full max-w-2xl bg-paper border border-rule rounded-sm shadow-xl p-8"
          data-testid="error-boundary"
        >
          <div className="flex items-start gap-4 mb-6">
            <div className="shrink-0 w-12 h-12 rounded-full bg-terracotta/10 border border-terracotta/40 flex items-center justify-center">
              <AlertTriangle className="w-6 h-6 text-terracotta" strokeWidth={1.5} />
            </div>
            <div>
              <h1 className="font-serif text-3xl text-ink mb-1">Something went wrong.</h1>
              <p className="text-sm text-ink-soft leading-relaxed">
                The editor crashed while rendering. Your work is safe — only the on-screen
                view broke. Try reloading; if it keeps happening, copy the details below
                and send them over.
              </p>
            </div>
          </div>

          <div
            className="bg-ink text-paper rounded-sm p-4 mb-6 max-h-64 overflow-auto font-mono text-xs leading-relaxed"
            data-testid="error-boundary-message"
          >
            <p className="text-terracotta-light mb-2 break-words whitespace-pre-wrap">
              {error?.message || String(error)}
            </p>
            {error?.stack && (
              <pre className="text-ink-mute whitespace-pre-wrap break-words">
                {error.stack.split('\n').slice(1, 8).join('\n')}
              </pre>
            )}
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => { this.reset(); window.location.reload(); }}
              data-testid="error-boundary-reload"
              className="inline-flex items-center gap-2 px-4 py-2 bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm text-sm font-medium transition-colors"
            >
              <RotateCcw className="w-4 h-4" /> Reload page
            </button>
            <Link
              to="/"
              onClick={this.reset}
              data-testid="error-boundary-home"
              className="inline-flex items-center gap-2 px-4 py-2 bg-ink/5 hover:bg-ink/10 border border-rule text-ink rounded-sm text-sm font-medium transition-colors"
            >
              <ArrowLeft className="w-4 h-4" /> Back to library
            </Link>
            <button
              type="button"
              onClick={this.copyDetails}
              data-testid="error-boundary-copy"
              className="inline-flex items-center gap-2 px-4 py-2 bg-ink/5 hover:bg-ink/10 border border-rule text-ink rounded-sm text-sm font-medium transition-colors ml-auto"
            >
              <Copy className="w-4 h-4" /> Copy details
            </button>
          </div>
        </div>
      </div>
    );
  }
}
