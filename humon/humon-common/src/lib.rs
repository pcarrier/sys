#![no_std]

use serde::{Deserialize, Serialize};

/// Maximum length for paths, command names, etc.
/// Note: These are eBPF perf buffer limits, not stack limits
pub const MAX_STRING_LEN: usize = 4096;  // PATH_MAX on Linux
pub const MAX_ARGV_LEN: usize = 8192;    // Accommodates long command lines

/// Event type discriminator
#[repr(u8)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum EventType {
    ProcessExec = 1,
    ProcessFork = 2,
    ProcessExit = 3,
    FileOpen = 10,
    FileRead = 11,
    FileWrite = 12,
    FileClose = 13,
    FileUnlink = 14,
    FileRename = 15,
    FileChmod = 16,
    FileChown = 17,
    NetConnect = 20,
    NetAccept = 21,
    NetBind = 22,
    NetSend = 23,
    NetRecv = 24,
    NetSocket = 25,
    InputKey = 30,
    InputMouse = 31,
    SecSetuid = 40,
    SecSetgid = 41,
    SecPtrace = 42,
    SecModuleLoad = 43,
    MemOomKill = 50,
    MemOomVictim = 51,
    NsUnshare = 52,
    NsSetns = 53,
    NsClone = 54,
    SignalSegfault = 55,
    SignalIllegal = 56,
    SignalAbort = 57,
    SignalGeneric = 58,
    ProcessCoredump = 59,
    NetConnectFail = 60,
    NetBindFail = 61,
    UsbAttach = 62,
    UsbDetach = 63,
    TtyAlloc = 64,
    PtyAlloc = 65,
    FsMount = 66,
    FsUmount = 67,
    SyscallFail = 68,
}

/// Core event wrapper
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub event_type: EventType,
    pub timestamp_ns: u64,
    pub pid: u32,
    pub tid: u32,
    pub uid: u32,
    pub gid: u32,
    pub data: EventData,
}

/// Event-specific data
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EventData {
    ProcessExec(ProcessExecEvent),
    ProcessFork(ProcessForkEvent),
    ProcessExit(ProcessExitEvent),
    FileOpen(FileOpenEvent),
    FileRead(FileReadEvent),
    FileWrite(FileWriteEvent),
    FileClose(FileCloseEvent),
    FileUnlink(FileUnlinkEvent),
    FileRename(FileRenameEvent),
    FileChmod(FileChmodEvent),
    FileChown(FileChownEvent),
    NetConnect(NetConnectEvent),
    NetAccept(NetAcceptEvent),
    NetBind(NetBindEvent),
    NetSend(NetSendEvent),
    NetRecv(NetRecvEvent),
    NetSocket(NetSocketEvent),
    InputKey(InputKeyEvent),
    InputMouse(InputMouseEvent),
    SecSetuid(SecSetuidEvent),
    SecSetgid(SecSetgidEvent),
    SecPtrace(SecPtraceEvent),
    SecModuleLoad(SecModuleLoadEvent),
    MemOomKill(MemOomKillEvent),
    MemOomVictim(MemOomVictimEvent),
    NsUnshare(NsUnshareEvent),
    NsSetns(NsSetnsEvent),
    NsClone(NsCloneEvent),
    SignalSegfault(SignalSegfaultEvent),
    SignalIllegal(SignalIllegalEvent),
    SignalAbort(SignalAbortEvent),
    SignalGeneric(SignalGenericEvent),
    ProcessCoredump(ProcessCoredumpEvent),
    NetConnectFail(NetConnectFailEvent),
    NetBindFail(NetBindFailEvent),
    UsbAttach(UsbAttachEvent),
    UsbDetach(UsbDetachEvent),
    TtyAlloc(TtyAllocEvent),
    PtyAlloc(PtyAllocEvent),
    FsMount(FsMountEvent),
    FsUmount(FsUmountEvent),
    SyscallFail(SyscallFailEvent),
}

// Process Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessExecEvent {
    pub filename: BoundedString<MAX_STRING_LEN>,
    pub argv: BoundedString<MAX_ARGV_LEN>,
    pub ppid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessForkEvent {
    pub child_pid: u32,
    pub ppid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessExitEvent {
    pub exit_code: i32,
}

// File Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileOpenEvent {
    pub path: BoundedString<MAX_STRING_LEN>,
    pub flags: i32,
    pub mode: u32,
    pub fd: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileReadEvent {
    pub fd: i32,
    pub count: u64,
    pub ret: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileWriteEvent {
    pub fd: i32,
    pub count: u64,
    pub ret: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileCloseEvent {
    pub fd: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileUnlinkEvent {
    pub path: BoundedString<MAX_STRING_LEN>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileRenameEvent {
    pub oldpath: BoundedString<MAX_STRING_LEN>,
    pub newpath: BoundedString<MAX_STRING_LEN>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileChmodEvent {
    pub path: BoundedString<MAX_STRING_LEN>,
    pub mode: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileChownEvent {
    pub path: BoundedString<MAX_STRING_LEN>,
    pub owner: u32,
    pub group: u32,
}

// Network Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetConnectEvent {
    pub family: u16,
    pub protocol: u8,
    pub local_addr: IpAddr,
    pub local_port: u16,
    pub remote_addr: IpAddr,
    pub remote_port: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetAcceptEvent {
    pub family: u16,
    pub local_addr: IpAddr,
    pub local_port: u16,
    pub remote_addr: IpAddr,
    pub remote_port: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetBindEvent {
    pub family: u16,
    pub addr: IpAddr,
    pub port: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetSendEvent {
    pub fd: i32,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetRecvEvent {
    pub fd: i32,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetSocketEvent {
    pub family: u16,
    pub socket_type: u32,
    pub protocol: u32,
}

// Input Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InputKeyEvent {
    pub key_code: u32,
    pub action: u8, // 0=release, 1=press, 2=repeat
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InputMouseEvent {
    pub button: u8,
    pub x: i32,
    pub y: i32,
}

// Security Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecSetuidEvent {
    pub old_uid: u32,
    pub new_uid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecSetgidEvent {
    pub old_gid: u32,
    pub new_gid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecPtraceEvent {
    pub target_pid: u32,
    pub request: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecModuleLoadEvent {
    pub name: BoundedString<MAX_STRING_LEN>,
}

// Memory Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemOomKillEvent {
    pub victim_pid: u32,
    pub victim_comm: BoundedString<MAX_STRING_LEN>,
    pub pages: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemOomVictimEvent {
    pub score: i64,
    pub total_vm: u64,
}

// Namespace Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NsUnshareEvent {
    pub flags: u64, // CLONE_NEW* flags
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NsSetnsEvent {
    pub fd: i32,
    pub nstype: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NsCloneEvent {
    pub flags: u64, // CLONE_NEW* flags
    pub child_pid: u32,
}

// Signal/Crash Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalSegfaultEvent {
    pub fault_addr: u64,
    pub ip: u64, // instruction pointer
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalIllegalEvent {
    pub ip: u64, // instruction pointer
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalAbortEvent {
    pub ip: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalGenericEvent {
    pub signal: i32,
    pub target_pid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessCoredumpEvent {
    pub signal: i32,
    pub comm: BoundedString<MAX_STRING_LEN>,
}

// Network Failure Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetConnectFailEvent {
    pub family: u16,
    pub remote_addr: IpAddr,
    pub remote_port: u16,
    pub error: i32, // errno (ECONNREFUSED, ETIMEDOUT, etc.)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetBindFailEvent {
    pub family: u16,
    pub addr: IpAddr,
    pub port: u16,
    pub error: i32, // errno (EADDRINUSE, etc.)
}

// USB Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsbAttachEvent {
    pub bus_num: u16,
    pub dev_num: u16,
    pub vendor_id: u16,
    pub product_id: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsbDetachEvent {
    pub bus_num: u16,
    pub dev_num: u16,
    pub vendor_id: u16,
    pub product_id: u16,
}

// TTY/PTY Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtyAllocEvent {
    pub name: BoundedString<MAX_STRING_LEN>,
    pub major: u32,
    pub minor: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PtyAllocEvent {
    pub name: BoundedString<MAX_STRING_LEN>,
    pub major: u32,
    pub minor: u32,
}

// Filesystem Events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FsMountEvent {
    pub dev_name: BoundedString<MAX_STRING_LEN>,
    pub path: BoundedString<MAX_STRING_LEN>,
    pub fs_type: BoundedString<256>,  // Filesystem type names (ext4, btrfs, etc.)
    pub flags: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FsUmountEvent {
    pub path: BoundedString<MAX_STRING_LEN>,
    pub flags: i32,
}

// Generic Syscall Failure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyscallFailEvent {
    pub syscall_nr: u64,
    pub error: i32, // errno (EPERM, EACCES, etc.)
}

// Helper types
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IpAddr {
    pub v6: [u8; 16], // Store as v6, v4-mapped for IPv4
}

impl IpAddr {
    pub fn from_v4(octets: [u8; 4]) -> Self {
        Self {
            v6: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff, octets[0], octets[1], octets[2], octets[3]],
        }
    }

    pub fn from_v6(octets: [u8; 16]) -> Self {
        Self { v6: octets }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoundedString<const N: usize> {
    pub data: [u8; N],
    pub len: usize,
}

impl<const N: usize> BoundedString<N> {
    pub fn new() -> Self {
        Self {
            data: [0u8; N],
            len: 0,
        }
    }

    pub fn from_bytes(bytes: &[u8]) -> Self {
        let mut s = Self::new();
        let copy_len = bytes.len().min(N);
        s.data[..copy_len].copy_from_slice(&bytes[..copy_len]);
        s.len = copy_len;
        s
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.data[..self.len]
    }
}

impl<const N: usize> Default for BoundedString<N> {
    fn default() -> Self {
        Self::new()
    }
}
