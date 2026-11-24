package engine

import "errors"

// DefaultMaxStdoutBytes caps buffered engine stdout (16 MiB). Excess bytes are
// drained so child pipes never stall, and Run reports ErrStdoutLimitExceeded.
const DefaultMaxStdoutBytes = 16 << 20

// DefaultMaxStderrBytes caps retained engine stderr (2 MiB), preserving the
// trailing bytes so tracebacks and strict-exit receipts survive verbose runs.
const DefaultMaxStderrBytes = 2 << 20

// maxPythonProbeOutputBytes caps stdout read during `python3 -I -S -c` version
// probing so an adversarial or misconfigured executable on PATH cannot exhaust
// memory before the probe timeout fires.
const maxPythonProbeOutputBytes = 4 << 10

// ErrStdoutLimitExceeded is returned by Run when subprocess stdout exceeds the
// configured MaxStdoutBytes (or DefaultMaxStdoutBytes) ceiling.
var ErrStdoutLimitExceeded = errors.New("engine: subprocess stdout exceeded limit")

func resolveOutputLimit(explicit, fallback int) int {
	if explicit > 0 {
		return explicit
	}
	return fallback
}

// appendCapped appends src to dst while never growing backing capacity past limit.
func appendCapped(dst, src []byte, limit int) []byte {
	need := len(dst) + len(src)
	if need <= cap(dst) {
		return append(dst, src...)
	}
	newCap := cap(dst) * 2
	if newCap < need {
		newCap = need
	}
	if newCap > limit {
		newCap = limit
	}
	grown := make([]byte, len(dst), newCap)
	copy(grown, dst)
	return append(grown, src...)
}

// headBuffer retains up to limit leading bytes and drains any excess bytes
// without returning a write error so child stdout pipes never deadlock.
type headBuffer struct {
	limit     int
	buf       []byte
	total     int64
	truncated bool
}

func newHeadBuffer(limit int) *headBuffer {
	if limit < 0 {
		limit = 0
	}
	return &headBuffer{limit: limit}
}

func (b *headBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if n == 0 {
		return 0, nil
	}
	b.total += int64(n)
	remaining := b.limit - len(b.buf)
	if remaining <= 0 {
		b.truncated = true
		return n, nil
	}
	if n <= remaining {
		b.buf = appendCapped(b.buf, p, b.limit)
		return n, nil
	}
	b.buf = appendCapped(b.buf, p[:remaining], b.limit)
	b.truncated = true
	return n, nil
}

func (b *headBuffer) Bytes() []byte   { return b.buf }
func (b *headBuffer) Truncated() bool { return b.truncated }
func (b *headBuffer) Total() int64    { return b.total }

// tailBuffer retains up to limit trailing bytes using lazy linear storage
// while under the limit and switching to a fixed-size ring buffer on overflow.
// Excess bytes are always drained without write errors so child pipes never stall.
type tailBuffer struct {
	limit     int
	buf       []byte
	ring      []byte
	pos       int
	total     int64
	truncated bool
}

func newTailBuffer(limit int) *tailBuffer {
	if limit < 0 {
		limit = 0
	}
	return &tailBuffer{limit: limit}
}

func (b *tailBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if n == 0 {
		return 0, nil
	}
	b.total += int64(n)
	if b.limit == 0 {
		b.truncated = true
		return n, nil
	}
	if !b.truncated && len(b.buf)+n <= b.limit {
		b.buf = appendCapped(b.buf, p, b.limit)
		return n, nil
	}
	b.truncated = true
	if b.ring == nil {
		b.ring = make([]byte, b.limit)
		if n >= b.limit {
			copy(b.ring, p[n-b.limit:])
			b.pos = 0
			b.buf = nil
			return n, nil
		}
		keepFromBuf := b.limit - n
		if keepFromBuf > len(b.buf) {
			keepFromBuf = len(b.buf)
		}
		copied := copy(b.ring, b.buf[len(b.buf)-keepFromBuf:])
		copy(b.ring[copied:], p)
		b.pos = (copied + n) % b.limit
		b.buf = nil
		return n, nil
	}
	if n >= b.limit {
		copy(b.ring, p[n-b.limit:])
		b.pos = 0
		return n, nil
	}
	first := copy(b.ring[b.pos:], p)
	if first < n {
		copy(b.ring, p[first:])
	}
	b.pos = (b.pos + n) % b.limit
	return n, nil
}

func (b *tailBuffer) Bytes() []byte {
	if !b.truncated {
		return b.buf
	}
	if b.limit == 0 || b.ring == nil {
		return nil
	}
	out := make([]byte, b.limit)
	tail := copy(out, b.ring[b.pos:])
	copy(out[tail:], b.ring[:b.pos])
	return out
}

func (b *tailBuffer) Truncated() bool { return b.truncated }
func (b *tailBuffer) Total() int64    { return b.total }
