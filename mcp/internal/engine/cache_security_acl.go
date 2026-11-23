package engine

import "strings"

const (
	windowsSystemSID         = "S-1-5-18"
	windowsAdministratorsSID = "S-1-5-32-544"

	// Access mask bits that allow modifying, deleting, or taking control of a
	// cached directory or Python file on Windows:
	// FILE_WRITE_DATA (0x2) | FILE_APPEND_DATA (0x4) | FILE_WRITE_EA (0x10) |
	// FILE_DELETE_CHILD (0x40) | FILE_WRITE_ATTRIBUTES (0x100) |
	// DELETE (0x10000) | WRITE_DAC (0x40000) | WRITE_OWNER (0x80000) |
	// GENERIC_ALL (0x10000000) | GENERIC_WRITE (0x40000000).
	windowsUntrustedWriteMask uint32 = 0x500D0156
)

func buildPrivateWindowsSDDL(userSID, ownerSID string) string {
	var b strings.Builder
	b.WriteString("D:P")
	if userSID != "" {
		b.WriteString("(A;OICI;FA;;;")
		b.WriteString(userSID)
		b.WriteString(")")
	}
	if ownerSID != "" &&
		!strings.EqualFold(ownerSID, userSID) &&
		!strings.EqualFold(ownerSID, windowsSystemSID) &&
		!strings.EqualFold(ownerSID, windowsAdministratorsSID) {
		b.WriteString("(A;OICI;FA;;;")
		b.WriteString(ownerSID)
		b.WriteString(")")
	}
	b.WriteString("(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")
	return b.String()
}

func isTrustedWindowsTrusteeSID(trusteeSID, userSID, ownerSID string) bool {
	if trusteeSID == "" {
		return false
	}
	return (userSID != "" && strings.EqualFold(trusteeSID, userSID)) ||
		(ownerSID != "" && strings.EqualFold(trusteeSID, ownerSID)) ||
		strings.EqualFold(trusteeSID, windowsSystemSID) ||
		strings.EqualFold(trusteeSID, windowsAdministratorsSID)
}

func hasUntrustedWindowsWriteAccess(mask uint32) bool {
	return mask&windowsUntrustedWriteMask != 0
}
