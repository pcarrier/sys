#![no_std]
#![no_main]

use aya_ebpf::{
    bindings::pt_regs,
    helpers::{bpf_get_current_pid_tgid, bpf_get_current_uid_gid, bpf_ktime_get_ns, bpf_probe_read_kernel, bpf_probe_read_user_str_bytes},
    macros::{kprobe, kretprobe, map, tracepoint},
    maps::{HashMap, PerfEventArray},
    programs::{ProbeContext, TracePointContext},
    EbpfContext,
};
use aya_log_ebpf::info;
use humon_common::*;

// Ring buffer for sending events to userspace
#[map]
static EVENTS: PerfEventArray<Event> = PerfEventArray::new(0);

// Helper to get current task info
#[inline(always)]
fn get_task_info() -> (u32, u32, u32, u32, u64) {
    let pid_tgid = unsafe { bpf_get_current_pid_tgid() };
    let uid_gid = unsafe { bpf_get_current_uid_gid() };
    let timestamp = unsafe { bpf_ktime_get_ns() };

    let pid = (pid_tgid >> 32) as u32;
    let tid = pid_tgid as u32;
    let uid = uid_gid as u32;
    let gid = (uid_gid >> 32) as u32;

    (pid, tid, uid, gid, timestamp)
}

// Process: execve
#[tracepoint]
pub fn trace_execve(ctx: TracePointContext) -> u32 {
    match try_trace_execve(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_execve(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    // Read filename from tracepoint args
    let filename_ptr: *const u8 = unsafe { ctx.read_at(16)? };
    let mut filename = BoundedString::<MAX_STRING_LEN>::new();

    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(filename_ptr as *const u8, &mut filename.data) } {
        filename.len = bytes.len();
    }

    // Read argv (limited implementation - full argv parsing requires task_struct access)
    let argv_ptr: *const *const u8 = unsafe { ctx.read_at(24)? };
    let mut argv = BoundedString::<MAX_ARGV_LEN>::new();

    // PPID requires task_struct access, not available from tracepoint context
    let ppid = 0;

    let event = Event {
        event_type: EventType::ProcessExec,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::ProcessExec(ProcessExecEvent {
            filename,
            argv,
            ppid,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Process: fork/clone
#[tracepoint]
pub fn trace_fork(ctx: TracePointContext) -> u32 {
    match try_trace_fork(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_fork(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let child_pid: u32 = unsafe { ctx.read_at(16)? };

    let event = Event {
        event_type: EventType::ProcessFork,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::ProcessFork(ProcessForkEvent {
            child_pid,
            ppid: pid,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Process: exit
#[tracepoint]
pub fn trace_exit(ctx: TracePointContext) -> u32 {
    match try_trace_exit(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_exit(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let exit_code: i32 = unsafe { ctx.read_at(16).unwrap_or(0) };

    let event = Event {
        event_type: EventType::ProcessExit,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::ProcessExit(ProcessExitEvent { exit_code }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// File: openat
#[kprobe]
pub fn trace_openat(ctx: ProbeContext) -> u32 {
    match try_trace_openat(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_openat(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    // openat(int dfd, const char *filename, int flags, umode_t mode)
    let filename_ptr: *const u8 = unsafe { ctx.arg(1).ok_or(1i64)? };
    let flags: i32 = unsafe { ctx.arg(2).ok_or(1i64)? };
    let mode: u32 = unsafe { ctx.arg(3).ok_or(1i64)? };

    let mut path = BoundedString::<MAX_STRING_LEN>::new();
    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(filename_ptr, &mut path.data) } {
        path.len = bytes.len();
    }

    let event = Event {
        event_type: EventType::FileOpen,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::FileOpen(FileOpenEvent {
            path,
            flags,
            mode,
            fd: -1, // Will be filled by kretprobe
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// File: read
#[kprobe]
pub fn trace_read(ctx: ProbeContext) -> u32 {
    match try_trace_read(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_read(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let fd: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let count: u64 = unsafe { ctx.arg(2).ok_or(1i64)? };

    let event = Event {
        event_type: EventType::FileRead,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::FileRead(FileReadEvent {
            fd,
            count,
            ret: 0,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// File: write
#[kprobe]
pub fn trace_write(ctx: ProbeContext) -> u32 {
    match try_trace_write(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_write(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let fd: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let count: u64 = unsafe { ctx.arg(2).ok_or(1i64)? };

    let event = Event {
        event_type: EventType::FileWrite,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::FileWrite(FileWriteEvent {
            fd,
            count,
            ret: 0,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Network: connect
#[kprobe]
pub fn trace_connect(ctx: ProbeContext) -> u32 {
    match try_trace_connect(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_connect(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    // Network address parsing requires sockaddr structure parsing (IPv4/IPv6)
    // This captures the event occurrence; use NetConnectFail for failures with details
    let event = Event {
        event_type: EventType::NetConnect,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::NetConnect(NetConnectEvent {
            family: 0,
            protocol: 0,
            local_addr: IpAddr::from_v4([0, 0, 0, 0]),
            local_port: 0,
            remote_addr: IpAddr::from_v4([0, 0, 0, 0]),
            remote_port: 0,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Security: setuid
#[kprobe]
pub fn trace_setuid(ctx: ProbeContext) -> u32 {
    match try_trace_setuid(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_setuid(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let new_uid: u32 = unsafe { ctx.arg(0).ok_or(1i64)? };

    let event = Event {
        event_type: EventType::SecSetuid,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::SecSetuid(SecSetuidEvent {
            old_uid: uid,
            new_uid,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// OOM: mark_victim tracepoint
#[tracepoint]
pub fn trace_oom_kill(ctx: TracePointContext) -> u32 {
    match try_trace_oom_kill(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_oom_kill(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let victim_pid: u32 = unsafe { ctx.read_at(8).unwrap_or(0) };
    let mut victim_comm = BoundedString::<MAX_STRING_LEN>::new();
    let pages: u64 = unsafe { ctx.read_at(24).unwrap_or(0) };

    let event = Event {
        event_type: EventType::MemOomKill,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::MemOomKill(MemOomKillEvent {
            victim_pid,
            victim_comm,
            pages,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Namespace: unshare
#[kprobe]
pub fn trace_unshare(ctx: ProbeContext) -> u32 {
    match try_trace_unshare(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_unshare(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let flags: u64 = unsafe { ctx.arg(0).ok_or(1i64)? };

    let event = Event {
        event_type: EventType::NsUnshare,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::NsUnshare(NsUnshareEvent { flags }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Namespace: setns
#[kprobe]
pub fn trace_setns(ctx: ProbeContext) -> u32 {
    match try_trace_setns(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_setns(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let fd: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let nstype: u32 = unsafe { ctx.arg(1).ok_or(1i64)? };

    let event = Event {
        event_type: EventType::NsSetns,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::NsSetns(NsSetnsEvent { fd, nstype }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Signal: signal_deliver tracepoint
#[tracepoint]
pub fn trace_signal(ctx: TracePointContext) -> u32 {
    match try_trace_signal(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_signal(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let signal: i32 = unsafe { ctx.read_at(8).unwrap_or(0) };
    let target_pid: u32 = unsafe { ctx.read_at(16).unwrap_or(0) };

    // Check for specific critical signals
    let event_type = match signal {
        11 => EventType::SignalSegfault, // SIGSEGV
        4 => EventType::SignalIllegal,   // SIGILL
        6 => EventType::SignalAbort,     // SIGABRT
        _ => EventType::SignalGeneric,
    };

    let data = match signal {
        11 => EventData::SignalSegfault(SignalSegfaultEvent {
            fault_addr: 0,
            ip: 0,
        }),
        4 => EventData::SignalIllegal(SignalIllegalEvent { ip: 0 }),
        6 => EventData::SignalAbort(SignalAbortEvent { ip: 0 }),
        _ => EventData::SignalGeneric(SignalGenericEvent { signal, target_pid }),
    };

    let event = Event {
        event_type,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data,
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Coredump: do_coredump
#[kprobe]
pub fn trace_coredump(ctx: ProbeContext) -> u32 {
    match try_trace_coredump(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_coredump(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let signal: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let mut comm = BoundedString::<MAX_STRING_LEN>::new();

    let event = Event {
        event_type: EventType::ProcessCoredump,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::ProcessCoredump(ProcessCoredumpEvent { signal, comm }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Network: connect failure (kretprobe)
#[kretprobe]
pub fn trace_connect_ret(ctx: ProbeContext) -> u32 {
    match try_trace_connect_ret(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_connect_ret(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let ret: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };

    // Only emit events for failures
    if ret < 0 {
        let event = Event {
            event_type: EventType::NetConnectFail,
            timestamp_ns: timestamp,
            pid,
            tid,
            uid,
            gid,
            data: EventData::NetConnectFail(NetConnectFailEvent {
                family: 0,
                remote_addr: IpAddr::from_v4([0, 0, 0, 0]),
                remote_port: 0,
                error: -ret,
            }),
        };

        unsafe {
            EVENTS.output(ctx, &event, 0);
        }
    }

    Ok(())
}

// Network: bind failure (kretprobe)
#[kretprobe]
pub fn trace_bind_ret(ctx: ProbeContext) -> u32 {
    match try_trace_bind_ret(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_bind_ret(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let ret: i32 = unsafe { ctx.arg(0).ok_or(1i64)? };

    // Only emit events for failures
    if ret < 0 {
        let event = Event {
            event_type: EventType::NetBindFail,
            timestamp_ns: timestamp,
            pid,
            tid,
            uid,
            gid,
            data: EventData::NetBindFail(NetBindFailEvent {
                family: 0,
                addr: IpAddr::from_v4([0, 0, 0, 0]),
                port: 0,
                error: -ret,
            }),
        };

        unsafe {
            EVENTS.output(ctx, &event, 0);
        }
    }

    Ok(())
}

// USB: device add
#[tracepoint]
pub fn trace_usb_add(ctx: TracePointContext) -> u32 {
    match try_trace_usb_add(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_usb_add(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let bus_num: u16 = unsafe { ctx.read_at(8).unwrap_or(0) };
    let dev_num: u16 = unsafe { ctx.read_at(10).unwrap_or(0) };
    let vendor_id: u16 = unsafe { ctx.read_at(12).unwrap_or(0) };
    let product_id: u16 = unsafe { ctx.read_at(14).unwrap_or(0) };

    let event = Event {
        event_type: EventType::UsbAttach,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::UsbAttach(UsbAttachEvent {
            bus_num,
            dev_num,
            vendor_id,
            product_id,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// USB: device remove
#[tracepoint]
pub fn trace_usb_remove(ctx: TracePointContext) -> u32 {
    match try_trace_usb_remove(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_usb_remove(ctx: &TracePointContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let bus_num: u16 = unsafe { ctx.read_at(8).unwrap_or(0) };
    let dev_num: u16 = unsafe { ctx.read_at(10).unwrap_or(0) };
    let vendor_id: u16 = unsafe { ctx.read_at(12).unwrap_or(0) };
    let product_id: u16 = unsafe { ctx.read_at(14).unwrap_or(0) };

    let event = Event {
        event_type: EventType::UsbDetach,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::UsbDetach(UsbDetachEvent {
            bus_num,
            dev_num,
            vendor_id,
            product_id,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// TTY: allocation
#[kprobe]
pub fn trace_tty_open(ctx: ProbeContext) -> u32 {
    match try_trace_tty_open(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_tty_open(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let mut name = BoundedString::<MAX_STRING_LEN>::new();

    let event = Event {
        event_type: EventType::TtyAlloc,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::TtyAlloc(TtyAllocEvent {
            name,
            major: 0,
            minor: 0,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// PTY: allocation
#[kprobe]
pub fn trace_pty_open(ctx: ProbeContext) -> u32 {
    match try_trace_pty_open(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_pty_open(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let mut name = BoundedString::<MAX_STRING_LEN>::new();

    let event = Event {
        event_type: EventType::PtyAlloc,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::PtyAlloc(PtyAllocEvent {
            name,
            major: 0,
            minor: 0,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Mount: sys_mount
#[kprobe]
pub fn trace_mount(ctx: ProbeContext) -> u32 {
    match try_trace_mount(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_mount(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let dev_name_ptr: *const u8 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let path_ptr: *const u8 = unsafe { ctx.arg(1).ok_or(1i64)? };
    let fs_type_ptr: *const u8 = unsafe { ctx.arg(2).ok_or(1i64)? };
    let flags: u64 = unsafe { ctx.arg(3).ok_or(1i64)? };

    let mut dev_name = BoundedString::<MAX_STRING_LEN>::new();
    let mut path = BoundedString::<MAX_STRING_LEN>::new();
    let mut fs_type = BoundedString::<256>::new();

    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(dev_name_ptr, &mut dev_name.data) } {
        dev_name.len = bytes.len();
    }
    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(path_ptr, &mut path.data) } {
        path.len = bytes.len();
    }
    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(fs_type_ptr, &mut fs_type.data) } {
        fs_type.len = bytes.len();
    }

    let event = Event {
        event_type: EventType::FsMount,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::FsMount(FsMountEvent {
            dev_name,
            path,
            fs_type,
            flags,
        }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

// Umount: sys_umount
#[kprobe]
pub fn trace_umount(ctx: ProbeContext) -> u32 {
    match try_trace_umount(&ctx) {
        Ok(()) => 0,
        Err(_) => 1,
    }
}

fn try_trace_umount(ctx: &ProbeContext) -> Result<(), i64> {
    let (pid, tid, uid, gid, timestamp) = get_task_info();

    let path_ptr: *const u8 = unsafe { ctx.arg(0).ok_or(1i64)? };
    let flags: i32 = unsafe { ctx.arg(1).ok_or(1i64)? };

    let mut path = BoundedString::<MAX_STRING_LEN>::new();
    if let Ok(bytes) = unsafe { bpf_probe_read_user_str_bytes(path_ptr, &mut path.data) } {
        path.len = bytes.len();
    }

    let event = Event {
        event_type: EventType::FsUmount,
        timestamp_ns: timestamp,
        pid,
        tid,
        uid,
        gid,
        data: EventData::FsUmount(FsUmountEvent { path, flags }),
    };

    unsafe {
        EVENTS.output(ctx, &event, 0);
    }

    Ok(())
}

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    unsafe { core::hint::unreachable_unchecked() }
}
