package humanize

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/go-rod/rod"
)

// FillDraft enters prepared text through browser input events, then verifies the
// editor. It never clears another draft or replays a partially completed input.
func FillDraft(ctx context.Context, elem *rod.Element, text string) error {
	return fillDraft(ctx, elem, text, false)
}

// FillPostDraft enters paragraphs in publishing editors that suppress Shift+Enter.
// Call only for a post body, never an Enter-to-send comment box.
func FillPostDraft(ctx context.Context, elem *rod.Element, text string) error {
	return fillDraft(ctx, elem, text, true)
}

func fillDraft(ctx context.Context, elem *rod.Element, text string, postBody bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	e := elem.Context(ctx)
	before, err := readDraft(e)
	if err != nil {
		return err
	}
	if normalizeDraft(before) == normalizeDraft(text) {
		return nil
	}
	if strings.TrimSpace(before) != "" {
		return fmt.Errorf("ERR_INPUT_NOT_EMPTY: 输入框已有不同内容，保留现场")
	}
	// Keep native input events. Verification surrounds paragraph entry; it
	// does not replace it with a bulk DOM assignment or a separate input path.
	if err := typeDraft(ctx, e, text, postBody); err != nil {
		return err
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		actual, err := readDraft(e)
		if err != nil {
			return err
		}
		if normalizeDraft(actual) == normalizeDraft(text) {
			return nil
		}
		if !time.Now().Before(deadline) {
			return fmt.Errorf("ERR_INPUT_MISMATCH: 输入后的文字或换行不匹配，未提交，保留现场")
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(40 * time.Millisecond):
		}
	}
}

func readDraft(elem *rod.Element) (string, error) {
	r, err := elem.Eval(`() => {
        if (typeof this.value === 'string') return this.value;
        const block = node => node.nodeType === 1 && /^(DIV|P|LI|BLOCKQUOTE|PRE|H[1-6])$/.test(node.tagName);
        const read = node => {
            if (node.nodeType === 3) return node.data;
            if (node.nodeType !== 1) return '';
            if (node.tagName === 'BR') return '\n';
            // A lone BR is the browser's caret placeholder in an empty paragraph.
            if (node.childNodes.length === 1 && node.firstChild.nodeName === 'BR') return '';
            let result = '', previousBlock = false, hasChild = false;
            for (const child of node.childNodes) {
                const isBlock = block(child);
                if (hasChild && (isBlock || previousBlock)) result += '\n';
                result += read(child);
                previousBlock = isBlock;
                hasChild = true;
            }
            return result;
        };
        return read(this);
    }`)
	if err != nil {
		return "", err
	}
	return r.Value.Str(), nil
}

func normalizeDraft(text string) string {
	// contenteditable may expose a final caret-only newline. Interior line breaks,
	// spaces, punctuation and Unicode characters must still match.
	return strings.TrimRight(strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\u00a0", " "), "\n")
}
