{ lib, pkgs, ... }:
let
  # AYN Technologies Linux 7.0 branch already carries the out-of-tree SM8750
  # support, the AYN Odin 3 device trees, and the Chipone ICNA35XX panel driver.
  # Use it as the kernel source so the device can boot with working display.
  aynLinux = pkgs.fetchFromGitHub {
    owner = "AYNTechnologies";
    repo = "linux";
    rev = "d0bd1239126dbd52b2bad91de1db0020de26977c";
    sha256 = "0hiy99rbl8w55wb722n2rz2qb2lb9588faxc9b8m2k3ivfbnqfnl";
  };
in
{
  boot = {
    kernelPackages = pkgs.linuxPackagesFor (
      pkgs.linuxKernel.kernels.linux_7_0.override {
        argsOverride = {
          version = "7.0.0";
          modDirVersion = "7.0.0";
          src = aynLinux;
        };
      }
    );

    # Kernel configuration for the AYN Odin 3 (Qualcomm SM8750 / CQ8725S).
    # These options enable the platform support and device drivers that are
    # not active by default in the generic NixOS aarch64 kernel config.
    kernelPatches = [
      {
        name = "odin3-config";
        patch = null;
        structuredExtraConfig = {
          # SM8750 platform
          ARCH_QCOM = lib.kernel.yes;
          ARM_QCOM_CPUFREQ_HW = lib.kernel.yes;
          ARM_QCOM_CPUFREQ_NVMEM = lib.kernel.yes;
          COMMON_CLK_QCOM = lib.kernel.yes;
          SM_GCC_8750 = lib.kernel.yes;
          SM_GPUCC_8750 = lib.kernel.yes;
          SM_DISPCC_8750 = lib.kernel.yes;
          PINCTRL_SM8750 = lib.kernel.yes;
          PINCTRL_QCOM_SPMI_PMIC = lib.kernel.yes;
          INTERCONNECT_QCOM = lib.kernel.yes;
          INTERCONNECT_QCOM_SM8750 = lib.kernel.yes;

          # Qualcomm core services
          QCOM_SMEM = lib.kernel.yes;
          QCOM_SMSM = lib.kernel.yes;
          QCOM_SMP2P = lib.kernel.yes;
          QCOM_PDC = lib.kernel.yes;
          QCOM_RPMH = lib.kernel.yes;
          QCOM_RPMHPD = lib.kernel.yes;
          QCOM_RPMPD = lib.kernel.yes;
          QCOM_COMMAND_DB = lib.kernel.yes;
          QCOM_CLK_RPMH = lib.kernel.yes;
          QCOM_CLK_SMD_RPM = lib.kernel.yes;
          QCOM_GENI_SE = lib.kernel.yes;
          QCOM_SPM = lib.kernel.yes;
          QCOM_IOMMU = lib.kernel.yes;
          QCOM_PMIC_GLINK = lib.kernel.module;

          # Storage
          SCSI_UFS_QCOM = lib.kernel.module;
          MMC_SDHCI_MSM = lib.kernel.yes;

          # Display
          DRM_MSM = lib.kernel.module;
          DRM_PANEL_CHIPONE_ICNA35XX = lib.kernel.module;
          DRM_PANEL_SIMPLE = lib.kernel.module;
          BACKLIGHT_QCOM_WLED = lib.kernel.module;

          # Input
          TOUCHSCREEN_EDT_FT5X06 = lib.kernel.module;
          TOUCHSCREEN_GOODIX = lib.kernel.module;
          TOUCHSCREEN_GOODIX_BERLIN_SPI = lib.kernel.module;
          JOYSTICK_XPAD = lib.kernel.module;
          JOYSTICK_XPAD_LEDS = lib.kernel.yes;

          # LEDs / fan
          LEDS_QCOM_LPG = lib.kernel.module;
          SENSORS_PWM_FAN = lib.kernel.module;

          # Audio
          SND_SOC_QCOM = lib.kernel.module;
          SND_SOC_QCOM_SDW = lib.kernel.module;
          SND_SOC_SM8250 = lib.kernel.module;
          SND_SOC_WCD938X = lib.kernel.module;
          SND_SOC_WCD938X_SDW = lib.kernel.module;
          SND_SOC_WCD939X = lib.kernel.module;
          SND_SOC_WCD939X_SDW = lib.kernel.module;
          SND_SOC_WCD_COMMON = lib.kernel.module;
          SND_SOC_WCD_MBHC = lib.kernel.module;
          SND_SOC_LPASS_RX_MACRO = lib.kernel.module;
          SND_SOC_LPASS_TX_MACRO = lib.kernel.module;
          SND_SOC_LPASS_VA_MACRO = lib.kernel.module;
          SND_SOC_LPASS_WSA_MACRO = lib.kernel.module;
          SOUNDWIRE_QCOM = lib.kernel.module;
          TYPEC_MUX_WCD939X_USBSS = lib.kernel.module;
          CLK_GFM_LPASS_SM8250 = lib.kernel.module;

          # Wireless / Bluetooth
          ATH12K = lib.kernel.module;
          BT_QCA = lib.kernel.module;
          BT_QCOMSMD = lib.kernel.module;
          BT_HCIUART_QCA = lib.kernel.yes;

          # Power / PMIC
          REGULATOR_QCOM_PM8008 = lib.kernel.module;
          REGULATOR_QCOM_REFGEN = lib.kernel.module;
          MFD_QCOM_PM8008 = lib.kernel.module;
          BATTERY_QCOM_BATTMGR = lib.kernel.module;
          QCOM_SPMI_VADC = lib.kernel.module;
          QCOM_SPMI_ADC5 = lib.kernel.module;
          QCOM_SPMI_ADC_TM5 = lib.kernel.module;
          QCOM_SPMI_TEMP_ALARM = lib.kernel.module;
          QCOM_WDT = lib.kernel.module;

          # Serial busses
          I2C_QCOM_CCI = lib.kernel.module;
          SPI_QCOM_GENI = lib.kernel.module;
          SPI_QCOM_QSPI = lib.kernel.module;

          # Remoteproc / modem / DSP
          QCOM_Q6V5_ADSP = lib.kernel.module;
          QCOM_Q6V5_MSS = lib.kernel.module;
          QCOM_Q6V5_PAS = lib.kernel.module;
          QCOM_WCNSS_PIL = lib.kernel.module;
          QCOM_WCNSS_CTRL = lib.kernel.module;
          QCOM_SYSMON = lib.kernel.module;

          # Type-C / UCSI / DP altmode
          TYPEC_TCPM = lib.kernel.module;
          TYPEC_TCPCI = lib.kernel.module;
          TYPEC_TPS6598X = lib.kernel.module;
          TYPEC_FUSB302 = lib.kernel.module;
          TYPEC_HD3SS3220 = lib.kernel.module;
          TYPEC_MUX_GPIO_SBU = lib.kernel.module;
          TYPEC_DP_ALTMODE = lib.kernel.module;
          UCSI_CCG = lib.kernel.module;
          UCSI_PMIC_GLINK = lib.kernel.module;
        };
      }
    ];

    initrd.availableKernelModules = [
      "sd_mod"
      "ufshcd_pltfrm"
      "ufs_qcom"
      "mmc_block"
      "sdhci_msm"
      "usb_storage"
      "uas"
    ];

    kernelParams = [
      "video=efifb:off"
      "console=tty0"
      "irqaffinity=0-1"
      "cgroup.memory=nokmem,nosocket"
      "nosoftlockup"
    ];
  };
}
