use anyhow::{anyhow, Context, Result};
use aya::{
    include_bytes_aligned,
    maps::perf::AsyncPerfEventArray,
    programs::{KProbe, TracePoint},
    util::online_cpus,
    Ebpf,
};
use aya_log::EbpfLogger;
use bytes::BytesMut;
use clap::Parser;
use futures::StreamExt;
use humon_common::Event;
use inotify::{Inotify, WatchMask};
use log::{debug, info, warn};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::sync::Arc;
use std::time::Duration;
use tokio::fs::OpenOptions;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;
use tokio::time::sleep;

#[derive(Parser, Debug)]
#[command(name = "humon")]
#[command(about = "Human monitoring - eBPF-based system event monitor", long_about = None)]
struct Args {
    /// Output file path for events
    #[arg(short, long, default_value = "events.log")]
    output: String,

    /// Watch mode: tail the event file instead of monitoring
    #[arg(short, long)]
    watch: bool,

    /// Enable verbose logging
    #[arg(short, long)]
    verbose: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    if args.verbose {
        env_logger::Builder::from_default_env()
            .filter_level(log::LevelFilter::Debug)
            .init();
    } else {
        env_logger::Builder::from_default_env()
            .filter_level(log::LevelFilter::Info)
            .init();
    }

    if args.watch {
        return watch_mode(&args.output).await;
    }

    info!("humon - System monitoring with eBPF");
    info!("=======================================");
    info!("WARNING: This captures sensitive system events including keyboard input.");
    info!("Only use on systems you own or have authorization to monitor.");
    info!("Starting in 3 seconds...\n");

    sleep(Duration::from_secs(3)).await;

    // Open output file for writing events
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&args.output)
        .await
        .context("Failed to open output file")?;

    let file = Arc::new(Mutex::new(file));
    info!("Writing events to: {}", args.output);

    // Load eBPF program
    info!("Loading eBPF programs...");
    let mut ebpf = load_ebpf()?;

    // Attach probes
    attach_probes(&mut ebpf)?;

    info!("eBPF programs loaded and attached");

    // Set up perf event array for receiving events
    let mut perf_array = AsyncPerfEventArray::try_from(ebpf.take_map("EVENTS").unwrap())?;

    // Spawn event processors for each CPU
    let cpus = online_cpus()?;
    info!("Processing events on {} CPUs", cpus.len());

    for cpu_id in cpus {
        let mut buf = perf_array.open(cpu_id, Some(32))?;
        let file_writer = file.clone();

        tokio::spawn(async move {
            let mut buffers = (0..10)
                .map(|_| BytesMut::with_capacity(8192))
                .collect::<Vec<_>>();

            loop {
                let events = match buf.read_events(&mut buffers).await {
                    Ok(events) => events,
                    Err(e) => {
                        warn!("Error reading events on CPU {}: {}", cpu_id, e);
                        continue;
                    }
                };

                for buf in buffers.iter().take(events.read) {
                    // Deserialize event from perf buffer
                    let event: Event = match postcard::from_bytes(buf) {
                        Ok(e) => e,
                        Err(e) => {
                            debug!("Failed to deserialize event: {}", e);
                            continue;
                        }
                    };

                    // Log event
                    log_event(&event);

                    // Serialize to postcard for file writing
                    let payload = match postcard::to_allocvec(&event) {
                        Ok(p) => p,
                        Err(e) => {
                            warn!("Failed to serialize event: {}", e);
                            continue;
                        }
                    };

                    // Write to file with length prefix for easy parsing
                    let mut file = file_writer.lock().await;
                    let len = payload.len() as u32;
                    if let Err(e) = file.write_all(&len.to_le_bytes()).await {
                        warn!("Failed to write event length to file: {}", e);
                        continue;
                    }
                    if let Err(e) = file.write_all(&payload).await {
                        warn!("Failed to write event to file: {}", e);
                        continue;
                    }
                    // Ensure data is written to disk
                    if let Err(e) = file.flush().await {
                        warn!("Failed to flush file: {}", e);
                    }
                }
            }
        });
    }

    info!("Event processors started");
    info!("\nMonitoring system events. Press Ctrl+C to exit.\n");

    // Keep running
    loop {
        sleep(Duration::from_secs(60)).await;
    }
}

fn load_ebpf() -> Result<Ebpf> {
    // Load the eBPF bytecode compiled by build.rs
    let mut ebpf = Ebpf::load(include_bytes_aligned!(
        concat!(env!("OUT_DIR"), "/humon.bpf.o")
    ))?;

    // Initialize logging
    if let Err(e) = EbpfLogger::init(&mut ebpf) {
        warn!("Failed to initialize eBPF logger: {}", e);
    }

    Ok(ebpf)
}

fn attach_probes(ebpf: &mut Ebpf) -> Result<()> {
    // Process tracepoints
    attach_tracepoint(ebpf, "trace_execve", "sched", "sched_process_exec")?;
    attach_tracepoint(ebpf, "trace_fork", "sched", "sched_process_fork")?;
    attach_tracepoint(ebpf, "trace_exit", "sched", "sched_process_exit")?;

    // File kprobes
    attach_kprobe(ebpf, "trace_openat", "do_sys_openat2", false)?;
    attach_kprobe(ebpf, "trace_openat_ret", "do_sys_openat2", true)?;
    attach_kprobe(ebpf, "trace_read", "vfs_read", false)?;
    attach_kprobe(ebpf, "trace_read_ret", "vfs_read", true)?;
    attach_kprobe(ebpf, "trace_write", "vfs_write", false)?;
    attach_kprobe(ebpf, "trace_write_ret", "vfs_write", true)?;

    // Network kprobes
    attach_kprobe(ebpf, "trace_connect", "__sys_connect", false)?;
    attach_kprobe(ebpf, "trace_connect_ret", "__sys_connect", true)?;
    attach_kprobe(ebpf, "trace_bind", "__sys_bind", false)?;
    attach_kprobe(ebpf, "trace_bind_ret", "__sys_bind", true)?;

    // Security kprobes
    attach_kprobe(ebpf, "trace_setuid", "sys_setuid", false)?;

    // OOM tracepoints
    attach_tracepoint(ebpf, "trace_oom_kill", "oom", "mark_victim")?;

    // Namespace kprobes
    attach_kprobe(ebpf, "trace_unshare", "sys_unshare", false)?;
    attach_kprobe(ebpf, "trace_setns", "sys_setns", false)?;

    // Signal tracepoint
    attach_tracepoint(ebpf, "trace_signal", "signal", "signal_deliver")?;

    // Coredump kprobe
    attach_kprobe(ebpf, "trace_coredump", "do_coredump", false)?;

    // USB tracepoints
    if let Err(e) = attach_tracepoint(ebpf, "trace_usb_add", "usb", "usb_device_add") {
        debug!("USB tracepoints not available: {}", e);
    }
    if let Err(e) = attach_tracepoint(ebpf, "trace_usb_remove", "usb", "usb_device_remove") {
        debug!("USB tracepoints not available: {}", e);
    }

    // TTY/PTY kprobes
    attach_kprobe(ebpf, "trace_tty_open", "tty_open", false)?;
    attach_kprobe(ebpf, "trace_pty_open", "pty_open", false)?;

    // Mount kprobes
    attach_kprobe(ebpf, "trace_mount", "sys_mount", false)?;
    attach_kprobe(ebpf, "trace_umount", "sys_umount", false)?;

    Ok(())
}

fn attach_tracepoint(ebpf: &mut Ebpf, prog_name: &str, category: &str, name: &str) -> Result<()> {
    let program: &mut TracePoint = ebpf.program_mut(prog_name).unwrap().try_into()?;
    program.load()?;
    program.attach(category, name)?;
    info!("Attached tracepoint: {}:{}", category, name);
    Ok(())
}

fn attach_kprobe(ebpf: &mut Ebpf, prog_name: &str, fn_name: &str, is_ret: bool) -> Result<()> {
    let program: &mut KProbe = ebpf.program_mut(prog_name).unwrap().try_into()?;
    program.load()?;
    let mut last_err = None;

    for candidate in candidate_kprobe_names(fn_name) {
        match program.attach(&candidate, 0) {
            Ok(()) => {
                let probe_type = if is_ret { "kretprobe" } else { "kprobe" };
                info!("Attached {}: {}", probe_type, candidate);
                return Ok(());
            }
            Err(e) => {
                last_err = Some(anyhow!(e).context(format!(
                    "failed attaching program {} to {}",
                    prog_name, candidate
                )));
            }
        }
    }

    Err(last_err.unwrap_or_else(|| {
        anyhow!(
            "unable to attach program {} using any known symbol for {}",
            prog_name,
            fn_name
        )
    }))
}

fn candidate_kprobe_names(fn_name: &str) -> Vec<String> {
    let mut names = vec![fn_name.to_string()];

    let mut push_unique = |value: String| {
        if !names.iter().any(|existing| existing == &value) {
            names.push(value);
        }
    };

    if let Some(rest) = fn_name.strip_prefix("__sys_") {
        push_unique(format!("sys_{}", rest));
        push_unique(format!("__x64_sys_{}", rest));
        push_unique(format!("__arm64_sys_{}", rest));
    }

    if let Some(rest) = fn_name.strip_prefix("sys_") {
        push_unique(format!("__x64_sys_{}", rest));
        push_unique(format!("__arm64_sys_{}", rest));
    }

    if let Some(rest) = fn_name.strip_prefix("do_sys_") {
        push_unique(format!("sys_{}", rest));
        push_unique(format!("__x64_sys_{}", rest));
        push_unique(format!("__arm64_sys_{}", rest));
    }

    if let Some(rest) = fn_name.strip_prefix("__x64_sys_") {
        push_unique(format!("sys_{}", rest));
        push_unique(format!("__sys_{}", rest));
    }

    if let Some(rest) = fn_name.strip_prefix("__arm64_sys_") {
        push_unique(format!("sys_{}", rest));
        push_unique(format!("__sys_{}", rest));
    }

    names
}

fn log_event(event: &Event) {
    use humon_common::{EventData, EventType};

    let timestamp_ms = event.timestamp_ns / 1_000_000;

    match &event.data {
        EventData::ProcessExec(e) => {
            let filename = String::from_utf8_lossy(e.filename.as_bytes());
            info!(
                "[{}ms] EXEC: pid={} uid={} file={:?}",
                timestamp_ms, event.pid, event.uid, filename
            );
        }
        EventData::ProcessFork(e) => {
            info!(
                "[{}ms] FORK: pid={} -> child_pid={}",
                timestamp_ms, event.pid, e.child_pid
            );
        }
        EventData::ProcessExit(e) => {
            info!(
                "[{}ms] EXIT: pid={} exit_code={}",
                timestamp_ms, event.pid, e.exit_code
            );
        }
        EventData::FileOpen(e) => {
            let path = String::from_utf8_lossy(e.path.as_bytes());
            debug!(
                "[{}ms] OPEN: pid={} path={:?} flags={:#x} fd={}",
                timestamp_ms, event.pid, path, e.flags, e.fd
            );
        }
        EventData::FileRead(e) => {
            debug!(
                "[{}ms] READ: pid={} fd={} requested={} ret={}",
                timestamp_ms, event.pid, e.fd, e.count, e.ret
            );
        }
        EventData::FileWrite(e) => {
            debug!(
                "[{}ms] WRITE: pid={} fd={} requested={} ret={}",
                timestamp_ms, event.pid, e.fd, e.count, e.ret
            );
        }
        EventData::NetConnect(e) => {
            info!(
                "[{}ms] CONNECT: pid={} fd={}",
                timestamp_ms, event.pid, e.fd
            );
        }
        EventData::SecSetuid(e) => {
            warn!(
                "[{}ms] SETUID: pid={} {} -> {}",
                timestamp_ms, event.pid, e.old_uid, e.new_uid
            );
        }
        EventData::MemOomKill(e) => {
            warn!(
                "[{}ms] OOM_KILL: victim_pid={} pages={}",
                timestamp_ms, e.victim_pid, e.pages
            );
        }
        EventData::MemOomVictim(e) => {
            warn!(
                "[{}ms] OOM_VICTIM: pid={} score={} total_vm={}",
                timestamp_ms, event.pid, e.score, e.total_vm
            );
        }
        EventData::NsUnshare(e) => {
            info!(
                "[{}ms] UNSHARE: pid={} flags={:#x}",
                timestamp_ms, event.pid, e.flags
            );
        }
        EventData::NsSetns(e) => {
            info!(
                "[{}ms] SETNS: pid={} fd={} nstype={:#x}",
                timestamp_ms, event.pid, e.fd, e.nstype
            );
        }
        EventData::NsClone(e) => {
            info!(
                "[{}ms] NS_CLONE: pid={} child_pid={} flags={:#x}",
                timestamp_ms, event.pid, e.child_pid, e.flags
            );
        }
        EventData::SignalSegfault(e) => {
            warn!(
                "[{}ms] SIGSEGV: pid={} fault_addr={:#x} ip={:#x}",
                timestamp_ms, event.pid, e.fault_addr, e.ip
            );
        }
        EventData::SignalIllegal(e) => {
            warn!(
                "[{}ms] SIGILL: pid={} ip={:#x}",
                timestamp_ms, event.pid, e.ip
            );
        }
        EventData::SignalAbort(e) => {
            warn!(
                "[{}ms] SIGABRT: pid={} ip={:#x}",
                timestamp_ms, event.pid, e.ip
            );
        }
        EventData::SignalGeneric(e) => {
            info!(
                "[{}ms] SIGNAL: sig={} target_pid={}",
                timestamp_ms, e.signal, e.target_pid
            );
        }
        EventData::ProcessCoredump(e) => {
            warn!(
                "[{}ms] COREDUMP: pid={} signal={}",
                timestamp_ms, event.pid, e.signal
            );
        }
        EventData::NetConnectFail(e) => {
            warn!(
                "[{}ms] CONNECT_FAIL: pid={} fd={} error={}",
                timestamp_ms, event.pid, e.fd, e.error
            );
        }
        EventData::NetBindFail(e) => {
            warn!(
                "[{}ms] BIND_FAIL: pid={} fd={} error={}",
                timestamp_ms, event.pid, e.fd, e.error
            );
        }
        EventData::UsbAttach(e) => {
            info!(
                "[{}ms] USB_ATTACH: bus={} dev={} vendor={:#x} product={:#x}",
                timestamp_ms, e.bus_num, e.dev_num, e.vendor_id, e.product_id
            );
        }
        EventData::UsbDetach(e) => {
            info!(
                "[{}ms] USB_DETACH: bus={} dev={} vendor={:#x} product={:#x}",
                timestamp_ms, e.bus_num, e.dev_num, e.vendor_id, e.product_id
            );
        }
        EventData::TtyAlloc(e) => {
            let name = String::from_utf8_lossy(e.name.as_bytes());
            info!(
                "[{}ms] TTY_ALLOC: pid={} name={:?}",
                timestamp_ms, event.pid, name
            );
        }
        EventData::PtyAlloc(e) => {
            let name = String::from_utf8_lossy(e.name.as_bytes());
            info!(
                "[{}ms] PTY_ALLOC: pid={} name={:?}",
                timestamp_ms, event.pid, name
            );
        }
        EventData::FsMount(e) => {
            let dev_name = String::from_utf8_lossy(e.dev_name.as_bytes());
            let path = String::from_utf8_lossy(e.path.as_bytes());
            let fs_type = String::from_utf8_lossy(e.fs_type.as_bytes());
            info!(
                "[{}ms] MOUNT: pid={} dev={:?} path={:?} type={:?}",
                timestamp_ms, event.pid, dev_name, path, fs_type
            );
        }
        EventData::FsUmount(e) => {
            let path = String::from_utf8_lossy(e.path.as_bytes());
            info!(
                "[{}ms] UMOUNT: pid={} path={:?}",
                timestamp_ms, event.pid, path
            );
        }
        EventData::SyscallFail(e) => {
            debug!(
                "[{}ms] SYSCALL_FAIL: pid={} nr={} error={}",
                timestamp_ms, event.pid, e.syscall_nr, e.error
            );
        }
        _ => {
            debug!("[{}ms] {:?}", timestamp_ms, event.event_type);
        }
    }
}

async fn watch_mode(file_path: &str) -> Result<()> {
    info!("Watch mode: tailing events from {}", file_path);

    // Open file for reading
    let mut file = File::open(file_path)
        .context("Failed to open file. Make sure the file exists.")?;

    // Read existing events first
    info!("Reading existing events...\n");
    read_and_display_events(&mut file)?;

    // Set up inotify to watch for file changes
    let mut inotify = Inotify::init()
        .context("Failed to initialize inotify")?;

    inotify.watches()
        .add(file_path, WatchMask::MODIFY)
        .context("Failed to add inotify watch")?;

    info!("\nWatching for new events... (Press Ctrl+C to exit)\n");

    let mut buffer = [0u8; 4096];
    let mut stream = inotify.into_event_stream(&mut buffer)?;

    // Wait for file modifications
    while let Some(event_or_error) = stream.next().await {
        match event_or_error {
            Ok(_) => {
                // File was modified, read new events
                if let Err(e) = read_and_display_events(&mut file) {
                    warn!("Error reading events: {}", e);
                }
            }
            Err(e) => {
                warn!("Inotify error: {}", e);
            }
        }
    }

    Ok(())
}

fn read_and_display_events(file: &mut File) -> Result<()> {
    loop {
        // Read length prefix (4 bytes)
        let mut len_buf = [0u8; 4];
        match file.read_exact(&mut len_buf) {
            Ok(_) => {},
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
                // Reached end of file
                break;
            }
            Err(e) => return Err(e.into()),
        }

        let len = u32::from_le_bytes(len_buf) as usize;

        // Read event data
        let mut event_buf = vec![0u8; len];
        file.read_exact(&mut event_buf)
            .context("Failed to read event data")?;

        // Deserialize event
        let event: Event = postcard::from_bytes(&event_buf)
            .context("Failed to deserialize event")?;

        // Display event
        log_event(&event);
    }

    Ok(())
}
