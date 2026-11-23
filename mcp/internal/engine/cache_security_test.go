package engine

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestEnsureRejectsTraversalAndUnsafeVersions(t *testing.T) {
	base := t.TempDir()
	sentinel := filepath.Join(base, "keep.txt")
	if err := os.WriteFile(sentinel, []byte("precious"), 0o600); err != nil {
		t.Fatal(err)
	}
	badVersions := []string{
		"",
		".",
		"..",
		"../other",
		"release/v1",
		`release\v1`,
		"v1..2",
		"v1.",
		"CON",
		"nul.txt",
		"-v1",
		"v1 v2",
	}
	for _, version := range badVersions {
		if _, err := Ensure(newTestFS(), base, version); err == nil {
			t.Fatalf("Ensure accepted unsafe version %q", version)
		}
	}
	mustReadFile(t, sentinel, "precious")
}

func TestEnsureDetectsTamperedCacheFileAndReExtracts(t *testing.T) {
	src := newTestFS()
	base := t.TempDir()

	cacheDir, err := Ensure(src, base, "v1")
	if err != nil {
		t.Fatalf("initial Ensure: %v", err)
	}
	target := filepath.Join(cacheDir, "last30days.py")
	if err := os.WriteFile(target, []byte("import os; os.system('evil')\n"), 0o600); err != nil {
		t.Fatalf("tamper cache: %v", err)
	}

	resetOnce(cacheDir)
	if _, err := Ensure(src, base, "v1"); err != nil {
		t.Fatalf("re-Ensure after tamper: %v", err)
	}
	mustReadFile(t, target, "# last30days entry\n")
}

func TestEnsureRejectsSymlinkParentCacheAndLock(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation requires elevated privileges on Windows")
	}
	src := newTestFS()

	t.Run("symlink parent", func(t *testing.T) {
		base := t.TempDir()
		decoy := t.TempDir()
		if err := os.Symlink(decoy, cacheParentDir(base)); err != nil {
			t.Fatal(err)
		}
		if _, err := Ensure(src, base, "v1"); err == nil {
			t.Fatal("expected error for symlink cache parent")
		}
	})

	t.Run("symlink cacheDir", func(t *testing.T) {
		base := t.TempDir()
		parent := cacheParentDir(base)
		if err := os.MkdirAll(parent, 0o700); err != nil {
			t.Fatal(err)
		}
		decoy := t.TempDir()
		if err := os.WriteFile(filepath.Join(decoy, SentinelFilename), []byte("v1"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(decoy, filepath.Join(parent, "v1")); err != nil {
			t.Fatal(err)
		}
		if _, err := Ensure(src, base, "v1"); err == nil {
			t.Fatal("expected error for symlink cacheDir")
		}
	})

	t.Run("symlink lock file", func(t *testing.T) {
		base := t.TempDir()
		parent := cacheParentDir(base)
		if err := os.MkdirAll(parent, 0o700); err != nil {
			t.Fatal(err)
		}
		target := filepath.Join(t.TempDir(), "victim.txt")
		if err := os.WriteFile(target, []byte("untouched"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(target, filepath.Join(parent, "v1.lock")); err != nil {
			t.Fatal(err)
		}
		if _, err := Ensure(src, base, "v1"); err == nil {
			t.Fatal("expected error for symlink lock file")
		}
		mustReadFile(t, target, "untouched")
	})

	t.Run("world-writable parent", func(t *testing.T) {
		base := t.TempDir()
		parent := cacheParentDir(base)
		if err := os.MkdirAll(parent, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(parent, 0o777); err != nil {
			t.Fatal(err)
		}
		if _, err := Ensure(src, base, "v1"); err == nil {
			t.Fatal("expected error for world-writable cache parent")
		}
	})
}

func TestEnsureReExtractsOnPlantedPythonOrPycacheOrWritableSubdir(t *testing.T) {
	src := newTestFS()

	t.Run("planted sitecustomize.py", func(t *testing.T) {
		base := t.TempDir()
		cacheDir, err := Ensure(src, base, "v1")
		if err != nil {
			t.Fatalf("initial Ensure: %v", err)
		}
		trojan := filepath.Join(cacheDir, "sitecustomize.py")
		if err := os.WriteFile(trojan, []byte("import os; os.system('evil')\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		resetOnce(cacheDir)
		if _, err := Ensure(src, base, "v1"); err != nil {
			t.Fatalf("re-Ensure: %v", err)
		}
		if _, err := os.Stat(trojan); !os.IsNotExist(err) {
			t.Fatalf("expected planted sitecustomize.py to be wiped on re-extract, stat err = %v", err)
		}
	})

	t.Run("planted pycache bytecode", func(t *testing.T) {
		base := t.TempDir()
		cacheDir, err := Ensure(src, base, "v1")
		if err != nil {
			t.Fatalf("initial Ensure: %v", err)
		}
		pycache := filepath.Join(cacheDir, "lib", "__pycache__")
		if err := os.MkdirAll(pycache, 0o700); err != nil {
			t.Fatal(err)
		}
		trojan := filepath.Join(pycache, "env.cpython-312.pyc")
		if err := os.WriteFile(trojan, []byte("evil bytecode"), 0o600); err != nil {
			t.Fatal(err)
		}
		resetOnce(cacheDir)
		if _, err := Ensure(src, base, "v1"); err != nil {
			t.Fatalf("re-Ensure: %v", err)
		}
		if _, err := os.Stat(pycache); !os.IsNotExist(err) {
			t.Fatalf("expected planted __pycache__ to be wiped on re-extract, stat err = %v", err)
		}
	})

	t.Run("world-writable subdir", func(t *testing.T) {
		if runtime.GOOS == "windows" {
			t.Skip("POSIX chmod bits")
		}
		base := t.TempDir()
		cacheDir, err := Ensure(src, base, "v1")
		if err != nil {
			t.Fatalf("initial Ensure: %v", err)
		}
		libDir := filepath.Join(cacheDir, "lib")
		if err := os.Chmod(libDir, 0o777); err != nil {
			t.Fatal(err)
		}
		resetOnce(cacheDir)
		if _, err := Ensure(src, base, "v1"); err != nil {
			t.Fatalf("re-Ensure: %v", err)
		}
		info, err := os.Stat(libDir)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm()&0o022 != 0 {
			t.Fatalf("expected libDir mode without group/world write after re-extract, got %04o", info.Mode().Perm())
		}
	})
}

func TestBuildEnvDisablesPythonBytecodeWrites(t *testing.T) {
	t.Setenv("PYTHONDONTWRITEBYTECODE", "0")
	out := buildEnv("/cache/dir", nil)
	var entries []string
	for _, kv := range out {
		if strings.HasPrefix(kv, "PYTHONDONTWRITEBYTECODE=") {
			entries = append(entries, kv)
		}
	}
	if len(entries) != 1 || entries[0] != "PYTHONDONTWRITEBYTECODE=1" {
		t.Fatalf("PYTHONDONTWRITEBYTECODE entries = %v, want [PYTHONDONTWRITEBYTECODE=1]", entries)
	}
}

func TestWindowsACLPolicyHelpers(t *testing.T) {
	userSID := "S-1-5-21-1000-2000-3000-1001"
	otherSID := "S-1-5-21-1000-2000-3000-1002"

	sddl := buildPrivateWindowsSDDL(userSID, windowsAdministratorsSID)
	wantSDDL := "D:P(A;OICI;FA;;;" + userSID + ")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
	if sddl != wantSDDL {
		t.Fatalf("buildPrivateWindowsSDDL = %q, want %q", sddl, wantSDDL)
	}

	for _, trusted := range []string{userSID, windowsSystemSID, windowsAdministratorsSID} {
		if !isTrustedWindowsTrusteeSID(trusted, userSID, windowsAdministratorsSID) {
			t.Fatalf("expected %q to be trusted", trusted)
		}
	}
	for _, untrusted := range []string{"", otherSID, "S-1-1-0", "S-1-5-11", "S-1-5-32-545"} {
		if isTrustedWindowsTrusteeSID(untrusted, userSID, windowsAdministratorsSID) {
			t.Fatalf("expected %q to be untrusted", untrusted)
		}
	}

	const fileReadOnlyMask uint32 = 0x00120089 // FILE_GENERIC_READ
	if hasUntrustedWindowsWriteAccess(fileReadOnlyMask) {
		t.Fatalf("read-only mask 0x%08x unexpectedly flagged as writable", fileReadOnlyMask)
	}
	for _, writeMask := range []uint32{
		0x00000002, // FILE_WRITE_DATA
		0x00000004, // FILE_APPEND_DATA
		0x00010000, // DELETE
		0x00040000, // WRITE_DAC
		0x00080000, // WRITE_OWNER
		0x40000000, // GENERIC_WRITE
		0x10000000, // GENERIC_ALL
	} {
		if !hasUntrustedWindowsWriteAccess(writeMask) {
			t.Fatalf("write mask 0x%08x was not flagged as untrusted write access", writeMask)
		}
	}
}
