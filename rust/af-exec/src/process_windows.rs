//! Windows spawn is suspended, assigned to a kill-on-close Job, checkpointed,
//! then resumed. No spawn-before-assignment execution window is accepted.
use super::*;
use std::ffi::c_void;
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
type Handle = *mut c_void;

#[repr(C)]
#[derive(Default)]
struct BasicLimits {
    process_time: i64,
    job_time: i64,
    flags: u32,
    min_working: usize,
    max_working: usize,
    active_limit: u32,
    affinity: usize,
    priority: u32,
    scheduling: u32,
}
#[repr(C)]
#[derive(Default)]
struct IoCounters {
    read_operations: u64,
    write_operations: u64,
    other_operations: u64,
    read_bytes: u64,
    write_bytes: u64,
    other_bytes: u64,
}
#[repr(C)]
#[derive(Default)]
struct ExtendedLimits {
    basic: BasicLimits,
    io: IoCounters,
    process_memory: usize,
    job_memory: usize,
    peak_process: usize,
    peak_job: usize,
}
#[repr(C)]
#[derive(Default)]
struct Accounting {
    user: i64,
    kernel: i64,
    period_user: i64,
    period_kernel: i64,
    faults: u32,
    total: u32,
    active: u32,
    terminated: u32,
}
#[repr(C)]
#[derive(Default)]
struct ThreadEntry {
    size: u32,
    usage: u32,
    thread: u32,
    owner: u32,
    priority: i32,
    delta: i32,
    flags: u32,
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> Handle;
    fn SetInformationJobObject(job: Handle, class: i32, info: *const c_void, size: u32) -> i32;
    fn QueryInformationJobObject(
        job: Handle,
        class: i32,
        info: *mut c_void,
        size: u32,
        returned: *mut u32,
    ) -> i32;
    fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
    fn TerminateJobObject(job: Handle, exit: u32) -> i32;
    fn CloseHandle(handle: Handle) -> i32;
    fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> Handle;
    fn Thread32First(snapshot: Handle, entry: *mut ThreadEntry) -> i32;
    fn Thread32Next(snapshot: Handle, entry: *mut ThreadEntry) -> i32;
    fn OpenThread(access: u32, inherit: i32, id: u32) -> Handle;
    fn ResumeThread(thread: Handle) -> u32;
}

pub fn prepare(command: &mut Command) -> ExecResult<()> {
    command.creation_flags(0x00000004 | 0x08000000); // CREATE_SUSPENDED | CREATE_NO_WINDOW
    Ok(())
}

pub struct Owner {
    job: Handle,
    attached: bool,
}
impl Owner {
    pub fn new() -> ExecResult<Self> {
        let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if job.is_null() {
            return Err(ExecError("Windows Job Object creation unavailable".into()));
        }
        let mut limits = ExtendedLimits::default();
        limits.basic.flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        let set = unsafe {
            SetInformationJobObject(
                job,
                9,
                &limits as *const _ as *const c_void,
                std::mem::size_of::<ExtendedLimits>() as u32,
            )
        };
        if set == 0 {
            unsafe {
                CloseHandle(job);
            }
            return Err(ExecError("Windows Job limits unavailable".into()));
        }
        Ok(Self {
            job,
            attached: false,
        })
    }
    pub fn attach(&mut self, child: &mut std::process::Child) -> ExecResult<()> {
        if unsafe { AssignProcessToJobObject(self.job, child.as_raw_handle()) } == 0 {
            return Err(ExecError(
                "Windows Job assignment refused; child remains suspended".into(),
            ));
        }
        self.attached = true;
        Ok(())
    }
    pub fn resume(&self, child: &mut std::process::Child) -> ExecResult<()> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(0x00000004, 0) }; // TH32CS_SNAPTHREAD
        if snapshot as isize == -1 {
            return Err(ExecError(
                "Windows primary thread inventory unavailable".into(),
            ));
        }
        let mut entry = ThreadEntry {
            size: std::mem::size_of::<ThreadEntry>() as u32,
            ..Default::default()
        };
        let mut found = Vec::new();
        let mut available = unsafe { Thread32First(snapshot, &mut entry) };
        while available != 0 {
            if entry.owner == child.id() {
                found.push(entry.thread);
            }
            available = unsafe { Thread32Next(snapshot, &mut entry) };
        }
        unsafe {
            CloseHandle(snapshot);
        }
        if found.len() != 1 {
            return Err(ExecError(
                "Windows suspended primary thread identity ambiguous".into(),
            ));
        }
        let thread = unsafe { OpenThread(0x0002, 0, found[0]) }; // THREAD_SUSPEND_RESUME
        if thread.is_null() {
            return Err(ExecError(
                "Windows primary thread access unavailable".into(),
            ));
        }
        let previous = unsafe { ResumeThread(thread) };
        unsafe {
            CloseHandle(thread);
        }
        if previous != 1 {
            return Err(ExecError(
                "Windows primary thread resume not witnessed".into(),
            ));
        }
        Ok(())
    }
    pub fn terminate(&self, child: &mut std::process::Child) -> bool {
        if !self.attached {
            let _ = child.kill();
            return child.wait().is_ok();
        }
        unsafe {
            TerminateJobObject(self.job, 1);
        }
        let until = Instant::now() + Duration::from_secs(2);
        loop {
            let mut accounting = Accounting::default();
            let known = unsafe {
                QueryInformationJobObject(
                    self.job,
                    1,
                    &mut accounting as *mut _ as *mut c_void,
                    std::mem::size_of::<Accounting>() as u32,
                    std::ptr::null_mut(),
                )
            } != 0;
            let parent = child.try_wait().ok().flatten().is_some();
            if known && accounting.active == 0 && parent {
                return true;
            }
            if Instant::now() >= until {
                return false;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}
impl Drop for Owner {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.job);
        }
    }
}
