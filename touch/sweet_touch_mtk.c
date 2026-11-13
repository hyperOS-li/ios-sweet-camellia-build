/*
 * camellia (MT6833 / Dimensity 700) Novatek NT36672C touch probe.
 *
 * Register model and bit-bang timing are taken from the working
 * YukinoXNU-Cannon (Redmi Note 9 5G / MT6853) port, whose touch wiring is
 * identical: SPI4@0x11018000, MI=GPIO12, MO=13, CLK=97, CS=98, reset=15,
 * IRQ=14, event page 0x21C00, swrst 0x3F0FE, fast-read 0x3F310.
 *
 * MTK GPIO ("Paris") model:
 *   mode  : 0x300 + (pin/8)*0x10, 4-bit field at (pin%8)*4
 *   dir   : 0x000 + (pin/32)*0x10 (status), 0x004 set output, 0x008 set input
 *   dout  : 0x100 + (pin/32)*0x10 (status), 0x104 set high, 0x108 set low
 *   din   : 0x200 + (pin/32)*0x10
 *
 * The probe only READS: chip trim id (0x3F004), the 17-byte Novatek state
 * block at event page + 0x50, the FWINFO block at +0x78 and the 65-byte
 * point report.  It never drives the reset pin and never resets the chip.
 *
 * Boot args:
 *   sweettouchmtk=1      enable this probe (default 0)
 *   sweettouchdelay=N    seconds before probing (default 30)
 *   sweetmtkdelay=US     half SPI clock period in microseconds (default 2)
 *   sweettouchsecs=N     how long to keep probing (default 1800)
 *   sweettouchperiod=MS  time between passes (default 2000)
 *
 * Output goes to the kernel console, prefix "SWEET TOUCH MTK:".
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>

#if defined(ARM64_BOARD_CONFIG_SWEET)

#include "sweet_nvt_fw_hex.h"

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
/* Mach MIG declaration is not included by BSD's exported thread.h. */
extern kern_return_t thread_terminate(thread_t);

#define GPIO_PA			0x10005000u
#define IOCFG_LM_PA		0x11E20000u	/* pin 14 pad regs */
#define IOCFG_BL_PA		0x11E60000u	/* pin 12 pad regs */

#define PIN_MI			12
#define PIN_MO			13
#define PIN_IRQ			14
#define PIN_RST			15
#define PIN_CLK			97
#define PIN_CS			98

#define GPIO_GROUP(pin)		(((pin) / 32u) * 0x10u)
#define GPIO_BIT(pin)		(1u << ((pin) % 32u))
#define GPIO_MODE(pin)		(0x300u + ((pin) / 8u) * 0x10u)
#define GPIO_MODE_SHIFT(pin)	(((pin) % 8u) * 4u)
#define GPIO_DIR(pin)		(0x000u + GPIO_GROUP(pin))
#define GPIO_DIR_SET(pin)	(0x004u + GPIO_GROUP(pin))
#define GPIO_DIR_CLR(pin)	(0x008u + GPIO_GROUP(pin))
#define GPIO_DO(pin)		(0x100u + GPIO_GROUP(pin))
#define GPIO_DO_SET(pin)	(0x104u + GPIO_GROUP(pin))
#define GPIO_DO_CLR(pin)	(0x108u + GPIO_GROUP(pin))
#define GPIO_DI(pin)		(0x200u + GPIO_GROUP(pin))

/* iocfg pad registers from pinctrl-mt6833.c: the value is the base offset of
 * the field; the SET register is base+4, the clear register is base. */
#define PAD_IES_MI		0x0090u, 11
#define PAD_IES_IRQ		0x0050u, 2

/* Novatek addresses (nova36672c/nt36xxx.h, confirmed by YukinoXNU-Cannon). */
#define NVT_EVENT		0x21C00u
#define NVT_EVENT_STATE		0x50u
#define NVT_STATE_LEN		17
#define NVT_EVENT_FWINFO	0x78u
#define NVT_FWINFO_LEN		11
#define NVT_CHIP_TRIM		0x03F004u
#define NVT_REPORT_LEN		65

static struct {
	vm_offset_t gpio, lm, bl;
	int started, dead;
	int half_us;
	uint32_t page;
	unsigned passes;
	int fw_result;
	unsigned fw_stage;
	unsigned fw_pid;
} st;

static int touch_chan;

extern void sweet_hid_touch(int finger, int down, int x, int y);

static uint8_t hid_down[10];
static uint16_t hid_last_x[10], hid_last_y[10];
static unsigned hid_events;

static uint32_t
g_rd(vm_offset_t base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(base + off);

	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

static void
g_wr(vm_offset_t base, uint32_t off, uint32_t val)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(base + off) = val;
	__asm__ volatile("dsb osh" ::: "memory");
}

static void
pin_mode(uint32_t pin, uint32_t mode)
{
	uint32_t off = GPIO_MODE(pin);
	uint32_t shift = GPIO_MODE_SHIFT(pin);
	uint32_t v = g_rd(st.gpio, off) & ~(0xFu << shift);

	g_wr(st.gpio, off, v | ((mode & 0xFu) << shift));
}

static void
pin_dir(uint32_t pin, bool out)
{
	g_wr(st.gpio, out ? GPIO_DIR_SET(pin) : GPIO_DIR_CLR(pin), GPIO_BIT(pin));
}

static void
pin_out(uint32_t pin, bool high)
{
	g_wr(st.gpio, high ? GPIO_DO_SET(pin) : GPIO_DO_CLR(pin), GPIO_BIT(pin));
}

static bool
pin_in(uint32_t pin)
{
	return (g_rd(st.gpio, GPIO_DI(pin)) & GPIO_BIT(pin)) != 0;
}

static uint32_t
pin_mode_get(uint32_t pin)
{
	return (g_rd(st.gpio, GPIO_MODE(pin)) >> GPIO_MODE_SHIFT(pin)) & 0xFu;
}

static uint32_t
pin_dir_get(uint32_t pin)
{
	return (g_rd(st.gpio, GPIO_DIR(pin)) >> ((pin) % 32u)) & 1u;
}

static uint32_t
pin_do_get(uint32_t pin)
{
	return (g_rd(st.gpio, GPIO_DO(pin)) >> ((pin) % 32u)) & 1u;
}

/* iocfg pad field: base is the clear register, base+4 the set register. */
static void
pad_set(vm_offset_t base, uint32_t off, unsigned bit, bool on)
{
	g_wr(base, on ? off + 4u : off, 1u << bit);
}

static void
touch_wait(unsigned ms)
{
	uint64_t deadline;

	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&touch_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

static void
spi_cs(bool low)
{
	pin_out(PIN_CS, low ? false : true);
	pin_out(PIN_CLK, false);
	delay(st.half_us);
}

/* Mode 0, MSB first, cannon's sampling order: MOSI is set while the clock is
 * low, MISO is sampled just BEFORE the rising edge, then the clock is pulsed.
 * Sampling after the edge reads the next bit (cannon saw FWINFO shifted). */
static uint8_t
spi_xfer(uint8_t out)
{
	uint8_t in = 0;

	for (int i = 7; i >= 0; i--) {
		pin_out(PIN_MO, ((out >> i) & 1u) != 0);
		delay(st.half_us);
		in = (uint8_t)((in << 1) | (pin_in(PIN_MI) ? 1u : 0u));
		pin_out(PIN_CLK, true);
		delay(st.half_us);
		pin_out(PIN_CLK, false);
	}
	return in;
}

/* 3-byte page select: FF page[22:15] page[14:7]. */
static void
nvt_set_page(uint32_t addr)
{
	spi_cs(true);
	spi_xfer(0xFF);
	spi_xfer((addr >> 15) & 0xFF);
	spi_xfer((addr >> 7) & 0xFF);
	spi_cs(false);
	delay(200);
}

/* Read len bytes at addr: offset (7 bit), dummy, then len data bytes. */
static void
nvt_read(uint32_t addr, uint8_t *buf, int len)
{
	nvt_set_page(addr);

	spi_cs(true);
	spi_xfer(addr & 0x7Fu);
	spi_xfer(0x00);
	for (int i = 0; i < len; i++) {
		buf[i] = spi_xfer(0x00);
	}
	spi_cs(false);
}

/* Same as nvt_read but assumes the event page is already latched. */
static void
nvt_read_latched(uint32_t addr, uint8_t *buf, int len)
{
	spi_cs(true);
	spi_xfer(addr & 0x7Fu);
	spi_xfer(0x00);
	for (int i = 0; i < len; i++) {
		buf[i] = spi_xfer(0x00);
	}
	spi_cs(false);
}

static unsigned
nonzero_bytes(const uint8_t *buf, int len)
{
	unsigned n = 0;

	for (int i = 0; i < len; i++) {
		if (buf[i] != 0x00 && buf[i] != 0xFF) {
			n++;
		}
	}
	return n;
}

static bool
fw_info_ok(const uint8_t *b, uint32_t max_x, uint32_t max_y)
{
	if ((uint8_t)(b[0] + b[1]) != 0xFF) {
		return false;
	}
	if ((((uint32_t)b[4] << 8) | b[5]) != max_x) {
		return false;
	}
	if ((((uint32_t)b[6] << 8) | b[7]) != max_y) {
		return false;
	}
	return true;
}

/* ---- Novatek RAM firmware boot ------------------------------------------
 * Ported from the vendor nt36xxx_fw_update.c and YukinoXNU-Cannon's
 * novatek_boot.c.  The image is the stock camellia
 * nt36672c_tm_01_ts_fw.bin (139264 bytes, NVT end flag at 0x1FFFD).
 */
struct nvt_sec {
	uint32_t sram, size, src, crc;
};

static uint8_t fwbuf[SWEET_NVT_FW_LEN];

static int
hex_nib(char c)
{
	if (c >= '0' && c <= '9') {
		return c - '0';
	}
	if (c >= 'a' && c <= 'f') {
		return c - 'a' + 10;
	}
	if (c >= 'A' && c <= 'F') {
		return c - 'A' + 10;
	}
	return -1;
}

static int
fw_decode(void)
{
	unsigned n = 0;

	for (unsigned i = 0; i + 1 < SWEET_NVT_FW_HEXLEN && n < SWEET_NVT_FW_LEN;
	    i += 2) {
		int a = hex_nib(sweet_nvt_fw_hex[i]);
		int b = hex_nib(sweet_nvt_fw_hex[i + 1]);

		if (a < 0 || b < 0) {
			return -1;
		}
		fwbuf[n++] = (uint8_t)((a << 4) | b);
	}
	return n == SWEET_NVT_FW_LEN ? 0 : -1;
}

static uint32_t
fw32(uint32_t off)
{
	return (uint32_t)fwbuf[off] | ((uint32_t)fwbuf[off + 1] << 8) |
	    ((uint32_t)fwbuf[off + 2] << 16) | ((uint32_t)fwbuf[off + 3] << 24);
}

static int
fw_parse(struct nvt_sec *sec, int *nsec)
{
	uint32_t end = fw32(0);
	uint32_t n = 0;
	uint32_t ovly_info, ovly_n, dlm_src;

	if (end < 0x40 || end > SWEET_NVT_FW_LEN) {
		return -1;
	}
	/* ILM and DLM: BIN, SRAM, size (12 bytes each), CRC at 0x18/0x1C. */
	for (uint32_t i = 0; i < 2; i++) {
		sec[n].src = fw32(i * 12);
		sec[n].sram = fw32(4 + i * 12);
		sec[n].size = fw32(8 + i * 12);
		sec[n].crc = fw32(0x18 + i * 4);
		n++;
	}
	/* Info sections: SRAM, size, BIN, CRC (16 bytes each from 0x30). */
	for (uint32_t off = 0x30; off + 16 <= end; off += 0x10) {
		sec[n].sram = fw32(off);
		sec[n].size = fw32(off + 4);
		sec[n].src = fw32(off + 8);
		sec[n].crc = fw32(off + 12);
		n++;
	}
	/* Overlay sections live at the DLM BIN address. */
	ovly_info = (fwbuf[0x28] >> 4) & 1u;
	ovly_n = ovly_info ? (fwbuf[0x28] & 0x0Fu) : 0;
	dlm_src = sec[1].src;
	for (uint32_t i = 0; i < ovly_n; i++) {
		uint32_t off = dlm_src + i * 16;

		if (off + 16 > SWEET_NVT_FW_LEN) {
			return -1;
		}
		sec[n].sram = fw32(off);
		sec[n].size = fw32(off + 4);
		sec[n].src = fw32(off + 8);
		sec[n].crc = fw32(off + 12);
		n++;
	}
	if (n > 24) {
		return -1;
	}
	for (uint32_t i = 0; i < n; i++) {
		if (sec[i].size == 0) {
			continue;
		}
		if (sec[i].src + sec[i].size + 1 > SWEET_NVT_FW_LEN ||
		    sec[i].sram + sec[i].size + 1 > 0x40000) {
			return -1;
		}
	}
	*nsec = (int)n;
	return 0;
}

static void
nvt_write(uint32_t addr, const uint8_t *data, unsigned n)
{
	nvt_set_page(addr);
	spi_cs(true);
	spi_xfer((uint8_t)((addr & 0x7Fu) | 0x80u));
	for (unsigned i = 0; i < n; i++) {
		spi_xfer(data[i]);
	}
	spi_cs(false);
}

static void
nvt_write8(uint32_t addr, uint8_t v)
{
	nvt_write(addr, &v, 1);
}

static void
nvt_write_le(uint32_t addr, uint32_t v, unsigned n)
{
	uint8_t b[4];

	for (unsigned i = 0; i < n; i++) {
		b[i] = (uint8_t)(v >> (8 * i));
	}
	nvt_write(addr, b, n);
}

static void
nvt_write_section(const struct nvt_sec *s)
{
	unsigned total = s->size + 1;	/* the vendor writes size+1 bytes */
	unsigned at = 0;

	while (at < total) {
		uint32_t addr = s->sram + at;
		unsigned room = 128u - (addr & 0x7Fu);
		unsigned n = total - at;

		if (n > 64) {
			n = 64;
		}
		if (n > room) {
			n = room;
		}
		nvt_write(addr, fwbuf + s->src + at, n);
		at += n;
	}
}

/* Returns 0 when the firmware runs and FWINFO/PID read back sanely. */
static int
nvt_boot_fw(void)
{
	struct nvt_sec sec[24];
	uint8_t id[8] = { 0 };
	uint8_t info[16] = { 0 };
	uint8_t pid[4] = { 0 };
	uint8_t v = 0;
	int nsec = 0, i;

	if (fw_decode()) {
		return -2;
	}
	st.fw_stage = 1;
	if (fwbuf[0x1FFFD] != 'N' || fwbuf[0x1FFFE] != 'V' ||
	    fwbuf[0x1FFFF] != 'T') {
		return -1;
	}
	if (fw_parse(sec, &nsec)) {
		return -2;
	}
	printf("SWEET TOUCH MTK: fw sections=%d ilm=%#x/%#x dlm=%#x/%#x\n",
	    nsec, sec[0].sram, sec[0].size, sec[1].sram, sec[1].size);

	/* Put the chip into its bootloader and adopt it. */
	st.fw_stage = 2;
	{
		int rstp = 0;

		(void)PE_parse_boot_argn("sweetmtkrst", &rstp, sizeof(rstp));
		if (rstp) {
			pin_mode(PIN_RST, 0);
			pin_dir(PIN_RST, true);
			pin_out(PIN_RST, false);
			delay(10 * 1000);
			pin_out(PIN_RST, true);
			delay(10 * 1000);
			printf("SWEET TOUCH MTK: fw reset pulse done\n");
		}
	}
	nvt_write8(0x3F0FE, 0x69);
	delay(5 * 1000);
	nvt_write8(0x3F310, 0x00);
	nvt_read(0x1F64E, id, 6);
	printf("SWEET TOUCH MTK: fw old trim %02x %02x %02x %02x %02x %02x\n",
	    id[0], id[1], id[2], id[3], id[4], id[5]);
	if (id[5] != 0x03 || id[3] != 0x72 || id[4] != 0x66) {
		return -3;
	}
	for (i = 0; i < nsec; i++) {
		if (sec[i].size) {
			nvt_write_section(&sec[i]);
		}
	}
	st.fw_stage = 3;
	printf("SWEET TOUCH MTK: fw written, setting crc banks\n");
	{
		unsigned bad = 0, first = 0, checked = 0;

		for (i = 0; i < nsec; i++) {
			uint8_t rb[32];
			unsigned offs[3];
			unsigned n = sec[i].size ? 32 : 0;

			if (!n) {
				continue;
			}
			if (n > sec[i].size) {
				n = sec[i].size;
			}
			offs[0] = 0;
			offs[1] = sec[i].size > n ? (sec[i].size - n) / 2 : 0;
			offs[2] = sec[i].size > n ? sec[i].size - n : 0;
			for (int k = 0; k < 3; k++) {
				nvt_read(sec[i].sram + offs[k], rb, n);
				checked++;
				for (unsigned j = 0; j < n; j++) {
					if (rb[j] != fwbuf[sec[i].src + offs[k] + j]) {
						if (!bad) {
							first = sec[i].sram + offs[k] + j;
						}
						bad++;
					}
				}
			}
		}
		printf("SWEET TOUCH MTK: fw verify checked=%u bad=%u first=%06x\n",
		    checked, bad, first);
	}

	/* ILM/DLM descriptors, lengths and CRCs (hw_crc = 2, 3-byte length). */
	nvt_write_le(0x3F128, sec[0].sram, 3);
	nvt_write_le(0x3F12C, sec[1].sram, 3);
	nvt_write_le(0x3F118, sec[0].size, 3);
	nvt_write_le(0x3F130, sec[1].size, 3);
	nvt_write_le(0x3F100, sec[0].crc, 4);
	nvt_write_le(0x3F104, sec[1].crc, 4);

	st.fw_stage = 4;
	{
		int crc_en = 0;
		uint8_t crcf = 0, rilm[4] = { 0 }, rdlm[4] = { 0 }, st;

		(void)PE_parse_boot_argn("sweetmtkcrc", &crc_en, sizeof(crc_en));
		printf("SWEET TOUCH MTK: fw crc_en=%d\n", crc_en);
		if (crc_en) {
			nvt_read(0x3F30E, &v, 1);
			nvt_write8(0x3F30E, (uint8_t)(v | 0x80));
		}
		nvt_write8(0x22D60, 0x00);
		if (crc_en) {
			nvt_write8(0x22D50, 0xAE);
		}
		nvt_write8(0x3F10D, 0x01);
		delay(5 * 1000);
		nvt_read(0x3F133, &crcf, 1);
		nvt_read(0x3F120, rilm, 4);
		nvt_read(0x3F124, rdlm, 4);
		printf("SWEET TOUCH MTK: fw crc flags=%02x ilm=%02x%02x%02x%02x/%08x "
		    "dlm=%02x%02x%02x%02x/%08x\n", crcf,
		    rilm[0], rilm[1], rilm[2], rilm[3], sec[0].crc,
		    rdlm[0], rdlm[1], rdlm[2], rdlm[3], sec[1].crc);
		for (int k = 0; k < 5; k++) {
			nvt_read(0x22D60, &st, 1);
			printf("SWEET TOUCH MTK: fw state[%d]=%02x\n", k, st);
			delay(100 * 1000);
		}
	}
	delay(5 * 1000);
	for (i = 0; i < 100; i++) {
		nvt_read(0x22D60, &v, 1);
		if (v >= 0xA0 && v <= 0xAF) {
			break;
		}
		delay(10 * 1000);
	}
	st.fw_stage = 5;
	printf("SWEET TOUCH MTK: fw reset state=%02x tries=%d\n", v, i);
	if (v < 0xA0 || v > 0xAF) {
		return -4;
	}
	st.fw_stage = 6;
	nvt_read(0x22D78, info, 12);
	printf("SWEET TOUCH MTK: fw info %02x %02x %02x %02x %02x%02x %02x%02x\n",
	    info[0], info[1], info[2], info[3], info[4], info[5], info[6],
	    info[7]);
	if ((uint8_t)(info[0] + info[1]) != 0xFF) {
		return -5;
	}
	st.fw_stage = 7;
	nvt_read(0x22D9A, pid, 2);
	st.fw_pid = (unsigned)((pid[1] << 8) | pid[0]);
	printf("SWEET TOUCH MTK: fw pid=%04x\n", st.fw_pid);
	return 0;
}

/* One 65-byte Novatek report -> 10 fingers -> SweetHID digitizer. */
static void
touch_hid_frame(const uint8_t *rep)
{
	uint8_t seen[10] = { 0 };
	unsigned i;
	bool idle = true;

	for (i = 0; i < 6; i++) {
		if (rep[i] != 0x77 && rep[i] != 0xFD && rep[i] != 0xFE) {
			idle = false;
			break;
		}
	}
	if (idle) {
		for (i = 0; i < 10; i++) {
			if (hid_down[i]) {
				sweet_hid_touch((int)i, 0, hid_last_x[i], hid_last_y[i]);
				hid_down[i] = 0;
			}
		}
		return;
	}

	for (i = 0; i < 10; i++) {
		const uint8_t *p = rep + i * 6;
		unsigned id = p[0] >> 3;
		unsigned state = p[0] & 0x07;
		unsigned x, y;

		if (id == 0 || id > 10 || (state != 1 && state != 2)) {
			continue;
		}
		x = ((unsigned)p[1] << 4) | (p[3] >> 4);
		y = ((unsigned)p[2] << 4) | (p[3] & 0x0F);
		if (x > 1079 || y > 2399) {
			continue;
		}
		seen[id - 1] = 1;
		if (!hid_down[id - 1]) {
			hid_down[id - 1] = 1;
			hid_events++;
			printf("SWEET TOUCH HID: down finger=%u at %u,%u\n",
			    id - 1, x, y);
		}
		hid_last_x[id - 1] = (uint16_t)x;
		hid_last_y[id - 1] = (uint16_t)y;
		sweet_hid_touch((int)(id - 1), 1, (int)x, (int)y);
	}

	for (i = 0; i < 10; i++) {
		if (hid_down[i] && !seen[i]) {
			sweet_hid_touch((int)i, 0, hid_last_x[i], hid_last_y[i]);
			hid_down[i] = 0;
			hid_events++;
			printf("SWEET TOUCH HID: up finger=%u at %u,%u\n",
			    i, hid_last_x[i], hid_last_y[i]);
		}
	}
}

static void
touch_hid_loop(void)
{
	unsigned poll_ms = 10, secs = 1800;
	uint8_t rep[NVT_REPORT_LEN];

	(void)PE_parse_boot_argn("sweettouchpoll", &poll_ms, sizeof(poll_ms));
	(void)PE_parse_boot_argn("sweettouchsecs", &secs, sizeof(secs));
	if (poll_ms < 4 || poll_ms > 500) {
		poll_ms = 10;
	}
	if (secs > 3600) {
		secs = 3600;
	}
	printf("SWEET TOUCH HID: polling page %05x every %u ms\n", st.page,
	    poll_ms);
	nvt_set_page(st.page);
	for (unsigned t = 0; t < secs * 1000u && !st.dead; t += poll_ms) {
		nvt_read_latched(st.page, rep, NVT_REPORT_LEN);
		touch_hid_frame(rep);
		if ((st.passes % 1000) == 0) {
			printf("SWEET TOUCH HID: pass %u rep %02x%02x%02x%02x "
			    "down=%u%u%u%u%u%u%u%u%u%u events=%u\n", st.passes,
			    rep[0], rep[1], rep[2], rep[3],
			    hid_down[0], hid_down[1], hid_down[2], hid_down[3],
			    hid_down[4], hid_down[5], hid_down[6], hid_down[7],
			    hid_down[8], hid_down[9], hid_events);
		}
		st.passes++;
		touch_wait(poll_ms);
	}
	printf("SWEET TOUCH HID: loop done after %u frames, %u events\n",
	    st.passes, hid_events);
}

static void
touch_probe_once(void)
{
	static const uint32_t pages[] = { NVT_EVENT, 0x22D00, 0x11A00, 0x1F600 };
	static const uint32_t max_x = 1080, max_y = 2400;
	uint8_t trim[8] = { 0 };
	uint8_t state[NVT_STATE_LEN] = { 0 };
	uint8_t info[NVT_FWINFO_LEN] = { 0 };
	uint8_t report[NVT_REPORT_LEN] = { 0 };
	unsigned irq = pin_in(PIN_IRQ) ? 1u : 0u;
	unsigned npages = sizeof(pages) / sizeof(pages[0]);

	nvt_read(NVT_CHIP_TRIM, trim, 6);
	nvt_read(NVT_EVENT + NVT_EVENT_STATE, state, NVT_STATE_LEN);

	if (st.page == 0) {
		for (unsigned i = 0; i < npages; i++) {
			nvt_read(pages[i] + NVT_EVENT_FWINFO, info, NVT_FWINFO_LEN);
			printf("SWEET TOUCH MTK: try %05x info %02x %02x %02x %02x "
			    "%02x%02x %02x%02x nz=%u%s\n", pages[i],
			    info[0], info[1], info[2], info[3], info[4], info[5],
			    info[6], info[7], nonzero_bytes(info, NVT_FWINFO_LEN),
			    fw_info_ok(info, max_x, max_y) ? " MATCH" : "");
			if (fw_info_ok(info, max_x, max_y)) {
				st.page = pages[i];
			}
		}
	}

	if (st.page != 0) {
		nvt_read(st.page + NVT_EVENT_FWINFO, info, NVT_FWINFO_LEN);
		nvt_read(st.page, report, NVT_REPORT_LEN);
		printf("SWEET TOUCH MTK: pass %u trim %02x%02x%02x%02x%02x%02x "
		    "irq=%u rst=%02x host=%02x hs=%02x page=%05x pid=%04x fw=%d/%u info "
		    "%02x%02x%02x%02x %02x%02x %02x%02x rep %02x%02x%02x%02x%02x%02x%02x%02x nz=%u\n",
		    st.passes, trim[0], trim[1], trim[2], trim[3], trim[4], trim[5],
		    irq, state[16], state[0], state[1], st.page, st.fw_pid, st.fw_result, st.fw_stage,
		    info[0], info[1], info[2], info[3], info[4], info[5], info[6],
		    info[7], report[0], report[1], report[2], report[3], report[4], report[5], report[6], report[7],
		    nonzero_bytes(report, NVT_REPORT_LEN));
	} else {
		printf("SWEET TOUCH MTK: pass %u trim %02x%02x%02x%02x%02x%02x "
		    "irq=%u rst=%02x host=%02x hs=%02x page=none\n", st.passes,
		    trim[0], trim[1], trim[2], trim[3], trim[4], trim[5], irq,
		    state[16], state[0], state[1]);
	}
	st.passes++;
}

static void
touch_thread(__unused void *arg, __unused wait_result_t wr)
{
	unsigned delay_s = 30, secs = 1800, period_ms = 2000;
	int half_us = 2;
	int fwboot = 0;
	int fwseen = 0;

	(void)PE_parse_boot_argn("sweettouchdelay", &delay_s, sizeof(delay_s));
	(void)PE_parse_boot_argn("sweettouchsecs", &secs, sizeof(secs));
	(void)PE_parse_boot_argn("sweettouchperiod", &period_ms, sizeof(period_ms));
	(void)PE_parse_boot_argn("sweetmtkdelay", &half_us, sizeof(half_us));
	fwseen = PE_parse_boot_argn("sweetmtkfw", &fwboot, sizeof(fwboot));
	if (!fwseen) {
		fwboot = 1;	/* probe build: boot the firmware unless told not to */
	}
	if (delay_s > 600) {
		delay_s = 600;
	}
	if (secs > 3600) {
		secs = 3600;
	}
	if (period_ms < 500 || period_ms > 60000) {
		period_ms = 2000;
	}
	if (half_us < 1 || half_us > 1000) {
		half_us = 2;
	}
	st.half_us = half_us;
	st.fw_result = 99;

	printf("SWEET TOUCH MTK: starting in %u s, half-clock %d us, fwboot=%d seen=%d\n",
	    delay_s, st.half_us, fwboot, fwseen);
	touch_wait(delay_s * 1000u);
	if (st.dead) {
		goto out;
	}

	st.gpio = ml_io_map(GPIO_PA, 0x1000);
	st.lm = ml_io_map(IOCFG_LM_PA, 0x1000);
	st.bl = ml_io_map(IOCFG_BL_PA, 0x1000);
	if (st.gpio == 0 || st.lm == 0 || st.bl == 0) {
		printf("SWEET TOUCH MTK: MMIO mapping failed\n");
		goto out;
	}
	printf("SWEET TOUCH MTK: mmio gpio=%p lm=%p bl=%p\n",
	    (void *)st.gpio, (void *)st.lm, (void *)st.bl);
	printf("SWEET TOUCH MTK: modes before mi=%u mo=%u irq=%u rst=%u clk=%u cs=%u\n",
	    pin_mode_get(PIN_MI), pin_mode_get(PIN_MO), pin_mode_get(PIN_IRQ),
	    pin_mode_get(PIN_RST), pin_mode_get(PIN_CLK), pin_mode_get(PIN_CS));

	/* Cannon's order: latch/levels first, then direction, then mux to GPIO.
	 * The reset pin is left untouched (only observed). */
	pin_out(PIN_CS, true);
	pin_out(PIN_CLK, false);
	pin_out(PIN_MO, false);
	pin_dir(PIN_CS, true);
	pin_dir(PIN_CLK, true);
	pin_dir(PIN_MO, true);
	pin_dir(PIN_MI, false);
	pin_dir(PIN_IRQ, false);
	pin_mode(PIN_MI, 0);
	pin_mode(PIN_MO, 0);
	pin_mode(PIN_IRQ, 0);
	pin_mode(PIN_CLK, 0);
	pin_mode(PIN_CS, 0);
	pad_set(st.bl, PAD_IES_MI, true);
	pad_set(st.lm, PAD_IES_IRQ, true);

	if (fwboot) {
		st.fw_result = nvt_boot_fw();
		printf("SWEET TOUCH MTK: fw boot result=%d stage=%u\n",
		    st.fw_result, st.fw_stage);
	}
	if (st.fw_result == 0) {
		int hid = 1;

		(void)PE_parse_boot_argn("sweettouchhid", &hid, sizeof(hid));
		st.page = 0x22D00u;	/* NT36675 event page, not the probe candidate */
		if (hid) {
			touch_hid_loop();
			goto out;
		}
	}

	printf("SWEET TOUCH MTK: gpio mi=%u/%u/%u/%u mo=%u/%u/%u/%u "
	    "irq=%u/%u/%u/%u rst=%u/%u/%u/%u clk=%u/%u/%u/%u cs=%u/%u/%u/%u\n",
	    pin_mode_get(PIN_MI), pin_dir_get(PIN_MI), pin_do_get(PIN_MI), pin_in(PIN_MI),
	    pin_mode_get(PIN_MO), pin_dir_get(PIN_MO), pin_do_get(PIN_MO), pin_in(PIN_MO),
	    pin_mode_get(PIN_IRQ), pin_dir_get(PIN_IRQ), pin_do_get(PIN_IRQ), pin_in(PIN_IRQ),
	    pin_mode_get(PIN_RST), pin_dir_get(PIN_RST), pin_do_get(PIN_RST), pin_in(PIN_RST),
	    pin_mode_get(PIN_CLK), pin_dir_get(PIN_CLK), pin_do_get(PIN_CLK), pin_in(PIN_CLK),
	    pin_mode_get(PIN_CS), pin_dir_get(PIN_CS), pin_do_get(PIN_CS), pin_in(PIN_CS));

	for (unsigned t = 0; t < secs * 1000u && !st.dead; t += period_ms) {
		touch_probe_once();
		touch_wait(period_ms);
	}
	printf("SWEET TOUCH MTK: probe done after %u passes\n", st.passes);

out:
	st.dead = true;
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

void
sweet_touch_mtk_init(void)
{
	int enabled = 0;
	thread_t thread;

	if (st.started) {
		return;
	}
	st.started = true;
	(void)PE_parse_boot_argn("sweettouchmtk", &enabled, sizeof(enabled));
	if (!enabled) {
		printf("SWEET TOUCH MTK: disabled (sweettouchmtk=0)\n");
		return;
	}
	if (kernel_thread_start(touch_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("SWEET TOUCH MTK: kernel_thread_start failed\n");
	}
}

#endif /* ARM64_BOARD_CONFIG_SWEET */
