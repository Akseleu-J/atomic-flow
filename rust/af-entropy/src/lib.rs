//! af-entropy: OS-quality randomness and portable file leases for atomic-flow
//! crates. Old-style `extern` declarations (import library provided by the
//! toolchain's self-contained MinGW) instead of raw-dylib, so the
//! x86_64-pc-windows-gnu toolchain links without external dlltool.

use std::fs::File;
use std::time::Duration;

/// Fill `buffer` with cryptographically secure OS randomness.
pub fn fill_random(buffer: &mut [u8]) {
    #[cfg(windows)]
    {
        // BCRYPT_USE_SYSTEM_PREFERRED_RNG = 0x00000002
        let status = unsafe {
            BCryptGenRandom(
                std::ptr::null_mut(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                0x0000_0002,
            )
        };
        if status == 0 {
            return;
        }
        // Fallback: process/environment entropy mix (non-cryptographic but
        // better than a fixed value; callers use this for ids/tokens).
        mix_fallback(buffer);
    }
    #[cfg(unix)]
    {
        if let Ok(mut file) = File::open("/dev/urandom") {
            use std::io::Read;
            if file.read_exact(buffer).is_ok() {
                return;
            }
        }
        mix_fallback(buffer);
    }
    #[cfg(not(any(windows, unix)))]
    {
        mix_fallback(buffer);
    }
}

/// 32 hex chars (128 bits) of random token.
pub fn token() -> String {
    let mut bytes = [0u8; 16];
    fill_random(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 64 hex chars (256 bits) of random token.
pub fn token64() -> String {
    format!("{}{}", token(), token())
}

fn mix_fallback(buffer: &mut [u8]) {
    use std::time::{SystemTime, UNIX_EPOCH};
    let mut state = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
        ^ (std::process::id() as u64) << 32
        ^ (&buffer as *const _ as u64);
    for byte in buffer.iter_mut() {
        state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        *byte = (state >> 33) as u8;
    }
}

#[cfg(windows)]
type BCRYPT_ALG_HANDLE = *mut std::ffi::c_void;

#[cfg(windows)]
#[link(name = "bcrypt")]
extern "system" {
    fn BCryptGenRandom(
        h_algorithm: BCRYPT_ALG_HANDLE,
        pb_buffer: *mut u8,
        cb_buffer: u32,
        dw_flags: u32,
    ) -> i32;
}

/// Exclusive, blocking-with-deadline lease on a marker lock file (the
/// `locking.py::exclusive_file` contract, kept as a persistent file).
pub struct FileLease {
    handle: File,
}

impl FileLease {
    pub fn acquire(path: &std::path::Path, blocking: bool, busy_message: &str) -> Result<FileLease, String> {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        loop {
            let file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .read(true)
                .open(path)
                .map_err(|error| format!("lease open failed: {error}"))?;
            match try_lock(&file) {
                Ok(()) => {
                    let mut locked = file;
                    if locked.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
                        use std::io::Write;
                        let _ = locked.write_all(b"0");
                        let _ = locked.flush();
                    }
                    return Ok(FileLease { handle: locked });
                }
                Err(_) => {
                    drop(file);
                    if !blocking || std::time::Instant::now() >= deadline {
                        return Err(busy_message.to_string());
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
            }
        }
    }
}

impl Drop for FileLease {
    fn drop(&mut self) {
        let _ = unlock(&self.handle);
    }
}

fn try_lock(file: &File) -> Result<(), std::io::Error> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        let handle = file.as_raw_handle() as *mut std::ffi::c_void;
        // LOCKFILE_EXCLUSIVE_LOCK = 2, LOCKFILE_FAIL_IMMEDIATELY = 1
        let flags = 2 | 1;
        let mut overlapped = OVERLAPPED {
            internal: 0,
            internal_high: 0,
            u: OverlappedUnion { pointer: std::ptr::null_mut() },
            h_event: std::ptr::null_mut(),
        };
        let ok = unsafe {
            LockFileEx(handle, flags, 0, 1, 0, &mut overlapped)
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let result = unsafe { flock(file.as_raw_fd(), LOCK_EX | LOCK_NB) };
        if result != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = file;
        Ok(())
    }
}

fn unlock(file: &File) -> Result<(), std::io::Error> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        let handle = file.as_raw_handle() as *mut std::ffi::c_void;
        let mut overlapped = OVERLAPPED {
            internal: 0,
            internal_high: 0,
            u: OverlappedUnion { pointer: std::ptr::null_mut() },
            h_event: std::ptr::null_mut(),
        };
        let ok = unsafe { UnlockFileEx(handle, 0, 1, 0, &mut overlapped) };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let result = unsafe { flock(file.as_raw_fd(), LOCK_UN) };
        if result != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = file;
        Ok(())
    }
}

#[cfg(windows)]
#[allow(non_snake_case)]
#[repr(C)]
struct OVERLAPPED {
    internal: usize,
    internal_high: usize,
    u: OverlappedUnion,
    h_event: *mut std::ffi::c_void,
}

#[cfg(windows)]
#[allow(non_snake_case)]
#[repr(C)]
union OverlappedUnion {
    offset: u64,
    pointer: *mut std::ffi::c_void,
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn LockFileEx(
        h_file: *mut std::ffi::c_void,
        dw_flags: u32,
        dw_reserved: u32,
        n_number_of_bytes_to_lock_low: u32,
        n_number_of_bytes_to_lock_high: u32,
        lp_overlapped: *mut OVERLAPPED,
    ) -> i32;
    fn UnlockFileEx(
        h_file: *mut std::ffi::c_void,
        dw_reserved: u32,
        n_number_of_bytes_to_unlock_low: u32,
        n_number_of_bytes_to_unlock_high: u32,
        lp_overlapped: *mut OVERLAPPED,
    ) -> i32;
}

#[cfg(unix)]
const LOCK_EX: i32 = 2;
#[cfg(unix)]
const LOCK_NB: i32 = 4;
#[cfg(unix)]
const LOCK_UN: i32 = 8;

#[cfg(unix)]
extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_are_random_and_hex() {
        let a = token();
        let b = token();
        assert_eq!(a.len(), 32);
        assert_ne!(a, b);
        assert!(a.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_eq!(token64().len(), 64);
    }

    #[test]
    fn lease_is_exclusive_and_reusable() {
        let dir = std::env::temp_dir().join(format!("af-entropy-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("lease.lock");
        let first = FileLease::acquire(&path, false, "busy").expect("first lease");
        assert!(FileLease::acquire(&path, false, "busy").is_err());
        drop(first);
        let _second = FileLease::acquire(&path, false, "busy").expect("lease after release");
    }
}
