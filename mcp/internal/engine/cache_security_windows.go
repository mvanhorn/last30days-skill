package engine

import (
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

const (
	fileAttributeReparsePoint = 0x0400

	sddlRevision1 = 1

	seFileObject            = 1
	ownerSecurityInfo       = 0x00000001
	daclSecurityInfo        = 0x00000004
	seDACLPresent    uint16 = 0x0004

	aclSizeInformationClass = 2
	accessAllowedACEType    = 0
	accessDeniedACEType     = 1

	tokenOwnerInfoClass = 4
)

var (
	modAdvapi32                              = syscall.NewLazyDLL("advapi32.dll")
	procConvertStringSecurityDescriptorToSDW = modAdvapi32.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW")
	procGetNamedSecurityInfoW                = modAdvapi32.NewProc("GetNamedSecurityInfoW")
	procGetSecurityInfo                      = modAdvapi32.NewProc("GetSecurityInfo")
	procGetSecurityDescriptorControl         = modAdvapi32.NewProc("GetSecurityDescriptorControl")
	procGetAclInformation                    = modAdvapi32.NewProc("GetAclInformation")
	procGetAce                               = modAdvapi32.NewProc("GetAce")
)

type windowsTokenOwner struct {
	Owner *syscall.SID
}

type windowsACLSizeInformation struct {
	AceCount      uint32
	AceBytesInUse uint32
	AceBytesFree  uint32
}

type windowsACEHeader struct {
	AceType  byte
	AceFlags byte
	AceSize  uint16
}

type windowsAccessAllowedACE struct {
	Header   windowsACEHeader
	Mask     uint32
	SidStart uint32
}

func sanitizeUserScope(raw string) string {
	var b strings.Builder
	for _, r := range raw {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '.' || r == '_' || r == '-' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func currentWindowsTokenSIDs() (string, string, error) {
	token, err := syscall.OpenCurrentProcessToken()
	if err != nil {
		return "", "", fmt.Errorf("engine: open process token: %w", err)
	}
	defer func() { _ = token.Close() }()

	tu, err := token.GetTokenUser()
	if err != nil || tu == nil || tu.User.Sid == nil {
		return "", "", fmt.Errorf("engine: read token user SID: %w", err)
	}
	userSID, err := tu.User.Sid.String()
	if err != nil {
		return "", "", fmt.Errorf("engine: format token user SID: %w", err)
	}

	ownerSID := userSID
	var needed uint32
	_ = syscall.GetTokenInformation(token, tokenOwnerInfoClass, nil, 0, &needed)
	if needed > 0 {
		buf := make([]byte, needed)
		if err := syscall.GetTokenInformation(token, tokenOwnerInfoClass, &buf[0], needed, &needed); err == nil {
			to := (*windowsTokenOwner)(unsafe.Pointer(&buf[0]))
			if to.Owner != nil {
				if s, sErr := to.Owner.String(); sErr == nil && s != "" {
					ownerSID = s
				}
			}
		}
	}
	return userSID, ownerSID, nil
}

func cacheParentDir(baseDir string) string {
	scope := ""
	if userSID, _, err := currentWindowsTokenSIDs(); err == nil {
		scope = sanitizeUserScope(userSID)
	}
	if scope == "" {
		if u, err := user.Current(); err == nil && u != nil {
			scope = sanitizeUserScope(u.Uid)
			if scope == "" {
				scope = sanitizeUserScope(u.Username)
			}
		}
	}
	if scope == "" {
		scope = sanitizeUserScope(os.Getenv("USERNAME"))
	}
	if scope == "" {
		return filepath.Join(filepath.Clean(baseDir), cacheSubdir)
	}
	return filepath.Join(filepath.Clean(baseDir), fmt.Sprintf("%s-%s", cacheSubdir, scope))
}

func isReparsePoint(info os.FileInfo) bool {
	if info.Mode()&os.ModeSymlink != 0 {
		return true
	}
	if attr, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		return attr.FileAttributes&fileAttributeReparsePoint != 0
	}
	return false
}

func mkdirPrivate(path string) error {
	userSID, ownerSID, err := currentWindowsTokenSIDs()
	if err != nil {
		return err
	}
	sddl := buildPrivateWindowsSDDL(userSID, ownerSID)
	sddlPtr, err := syscall.UTF16PtrFromString(sddl)
	if err != nil {
		return err
	}
	var sd uintptr
	r1, _, callErr := procConvertStringSecurityDescriptorToSDW.Call(
		uintptr(unsafe.Pointer(sddlPtr)),
		uintptr(sddlRevision1),
		uintptr(unsafe.Pointer(&sd)),
		0,
	)
	if r1 == 0 {
		return fmt.Errorf("engine: build private Windows security descriptor: %w", callErr)
	}
	defer func() { _, _ = syscall.LocalFree(syscall.Handle(sd)) }()

	pathPtr, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	sa := syscall.SecurityAttributes{
		Length:             uint32(unsafe.Sizeof(syscall.SecurityAttributes{})),
		SecurityDescriptor: sd,
	}
	if err := syscall.CreateDirectory(pathPtr, &sa); err != nil {
		return &os.PathError{Op: "mkdir", Path: path, Err: err}
	}
	return nil
}

func ensurePrivateParentDir(parent string) error {
	if err := os.MkdirAll(filepath.Dir(parent), 0o700); err != nil {
		return err
	}
	if err := mkdirPrivate(parent); err != nil && !errors.Is(err, os.ErrExist) && !errors.Is(err, syscall.ERROR_ALREADY_EXISTS) {
		return err
	}
	return verifyPrivateDir(parent)
}

func verifyWindowsObjectSecurity(path string) error {
	pathPtr, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	var (
		ownerSIDPtr *syscall.SID
		dacl        uintptr
		sd          uintptr
	)
	status, _, _ := procGetNamedSecurityInfoW.Call(
		uintptr(unsafe.Pointer(pathPtr)),
		uintptr(seFileObject),
		uintptr(ownerSecurityInfo|daclSecurityInfo),
		uintptr(unsafe.Pointer(&ownerSIDPtr)),
		0,
		uintptr(unsafe.Pointer(&dacl)),
		0,
		uintptr(unsafe.Pointer(&sd)),
	)
	if status != 0 {
		return fmt.Errorf("engine: read Windows security info for %s: %w", path, syscall.Errno(status))
	}
	defer func() { _, _ = syscall.LocalFree(syscall.Handle(sd)) }()
	return verifyWindowsSecurityDescriptor(path, ownerSIDPtr, dacl, sd)
}

func verifyWindowsHandleSecurity(handle syscall.Handle, path string) error {
	var (
		ownerSIDPtr *syscall.SID
		dacl        uintptr
		sd          uintptr
	)
	status, _, _ := procGetSecurityInfo.Call(
		uintptr(handle),
		uintptr(seFileObject),
		uintptr(ownerSecurityInfo|daclSecurityInfo),
		uintptr(unsafe.Pointer(&ownerSIDPtr)),
		0,
		uintptr(unsafe.Pointer(&dacl)),
		0,
		uintptr(unsafe.Pointer(&sd)),
	)
	if status != 0 {
		return fmt.Errorf("engine: read Windows handle security info for %s: %w", path, syscall.Errno(status))
	}
	defer func() { _, _ = syscall.LocalFree(syscall.Handle(sd)) }()
	return verifyWindowsSecurityDescriptor(path, ownerSIDPtr, dacl, sd)
}

func verifyWindowsSecurityDescriptor(path string, ownerSIDPtr *syscall.SID, dacl, sd uintptr) error {
	userSID, tokenOwnerSID, err := currentWindowsTokenSIDs()
	if err != nil {
		return err
	}
	if ownerSIDPtr == nil {
		return fmt.Errorf("engine: cache path %s has no owner SID", path)
	}
	objOwner, err := ownerSIDPtr.String()
	if err != nil {
		return fmt.Errorf("engine: decode owner SID for %s: %w", path, err)
	}
	if !strings.EqualFold(objOwner, userSID) && !strings.EqualFold(objOwner, tokenOwnerSID) {
		return fmt.Errorf("engine: cache path %s is owned by unexpected principal %s", path, objOwner)
	}

	var (
		control uint16
		rev     uint32
	)
	r1, _, callErr := procGetSecurityDescriptorControl.Call(
		sd,
		uintptr(unsafe.Pointer(&control)),
		uintptr(unsafe.Pointer(&rev)),
	)
	if r1 == 0 {
		return fmt.Errorf("engine: inspect security descriptor control for %s: %w", path, callErr)
	}
	if control&seDACLPresent == 0 || dacl == 0 {
		return fmt.Errorf("engine: cache path %s has a null or missing DACL", path)
	}

	var aclSize windowsACLSizeInformation
	r1, _, callErr = procGetAclInformation.Call(
		dacl,
		uintptr(unsafe.Pointer(&aclSize)),
		uintptr(unsafe.Sizeof(aclSize)),
		uintptr(aclSizeInformationClass),
	)
	if r1 == 0 {
		return fmt.Errorf("engine: read DACL size for %s: %w", path, callErr)
	}

	minACESize := uint16(unsafe.Sizeof(windowsAccessAllowedACE{}))
	for i := uint32(0); i < aclSize.AceCount; i++ {
		var hdr *windowsACEHeader
		r1, _, callErr = procGetAce.Call(dacl, uintptr(i), uintptr(unsafe.Pointer(&hdr)))
		if r1 == 0 || hdr == nil {
			return fmt.Errorf("engine: read DACL entry %d for %s: %w", i, path, callErr)
		}
		if hdr.AceType == accessDeniedACEType {
			continue
		}
		if hdr.AceType != accessAllowedACEType {
			return fmt.Errorf("engine: cache path %s has unsupported ACE type %d", path, hdr.AceType)
		}
		if hdr.AceSize < minACESize {
			return fmt.Errorf("engine: cache path %s has malformed ACE size %d", path, hdr.AceSize)
		}
		ace := (*windowsAccessAllowedACE)(unsafe.Pointer(hdr))
		trustee := (*syscall.SID)(unsafe.Pointer(&ace.SidStart))
		trusteeSID, err := trustee.String()
		if err != nil {
			return fmt.Errorf("engine: decode ACE trustee SID for %s: %w", path, err)
		}
		if !isTrustedWindowsTrusteeSID(trusteeSID, userSID, tokenOwnerSID) && hasUntrustedWindowsWriteAccess(ace.Mask) {
			return fmt.Errorf("engine: cache path %s grants write access to untrusted principal %s (mask 0x%08x)", path, trusteeSID, ace.Mask)
		}
	}
	return nil
}

func verifyPrivateDir(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if isReparsePoint(info) {
		return fmt.Errorf("engine: refusing symlink or junction cache directory %s", path)
	}
	if !info.IsDir() {
		return fmt.Errorf("engine: cache path %s is not a directory", path)
	}
	return verifyWindowsObjectSecurity(path)
}

func verifyOwnedRegularFile(info os.FileInfo, path string) error {
	if isReparsePoint(info) || !info.Mode().IsRegular() {
		return fmt.Errorf("engine: refusing non-regular cache file %s", path)
	}
	return verifyWindowsObjectSecurity(path)
}

func openNoFollowFile(path string, flag int, perm os.FileMode) (*os.File, error) {
	if info, err := os.Lstat(path); err == nil {
		if err := verifyOwnedRegularFile(info, path); err != nil {
			return nil, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	file, err := os.OpenFile(path, flag, perm)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if !info.Mode().IsRegular() {
		_ = file.Close()
		return nil, fmt.Errorf("engine: refusing non-regular cache file %s", path)
	}
	if err := verifyWindowsHandleSecurity(syscall.Handle(file.Fd()), path); err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}

func openRootRegularNoFollow(root *os.Root, cacheDir, nativeRel string) (*os.File, error) {
	fullPath := filepath.Join(cacheDir, nativeRel)
	linfo, err := root.Lstat(nativeRel)
	if err != nil {
		return nil, err
	}
	if isReparsePoint(linfo) || !linfo.Mode().IsRegular() {
		return nil, fmt.Errorf("engine: refusing non-regular cache file %s", fullPath)
	}
	file, err := root.Open(nativeRel)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if !info.Mode().IsRegular() {
		_ = file.Close()
		return nil, fmt.Errorf("engine: refusing non-regular cache file %s", fullPath)
	}
	if err := verifyWindowsHandleSecurity(syscall.Handle(file.Fd()), fullPath); err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}
