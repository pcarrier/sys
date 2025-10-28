use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());

    // Build eBPF program
    let ebpf_dir = PathBuf::from("humon-ebpf");

    println!("cargo:rerun-if-changed=humon-ebpf/src");

    let status = Command::new("cargo")
        .args(&[
            "build",
            "--release",
            "--target=bpfel-unknown-none",
            "-Z", "build-std=core",
        ])
        .current_dir(&ebpf_dir)
        .status()
        .expect("Failed to build eBPF program");

    if !status.success() {
        panic!("eBPF build failed");
    }

    // Copy the built eBPF object to OUT_DIR
    let ebpf_obj = ebpf_dir.join("target/bpfel-unknown-none/release/humon");
    let dest = out_dir.join("humon.bpf.o");

    std::fs::copy(&ebpf_obj, &dest).expect("Failed to copy eBPF object");
}
