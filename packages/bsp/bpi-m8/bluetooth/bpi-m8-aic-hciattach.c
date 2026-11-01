// SPDX-License-Identifier: GPL-2.0
/*
 * BPI-M8 D80 H4 UART attachment. The command sequence and AON parameters
 * follow the platform SDK's hciattach_aic.c protocol. The combo firmware is
 * loaded by the SDIO driver before this program runs.
 *
 * This process owns its UART until SIGTERM/SIGINT. It never power-cycles the
 * combo chip and never substitutes a shared fallback Bluetooth address.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#ifndef N_HCI
#define N_HCI 15
#endif
#define HCIUARTSETPROTO _IOW('U', 200, int)
#define HCI_UART_H4 0
#define COMMAND_TIMEOUT_MS 3000

static volatile sig_atomic_t stopped;

static void stop_handler(int signal_number)
{
	stopped = signal_number;
}

static int64_t monotonic_ms(void)
{
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int transfer(int fd, void *buffer, size_t count, int writing,
		    int64_t deadline)
{
	uint8_t *bytes = buffer;
	while (count && !stopped) {
		int64_t remaining = deadline - monotonic_ms();
		struct pollfd descriptor = { .fd = fd, .events = writing ? POLLOUT : POLLIN };
		ssize_t done;
		int result;

		if (remaining <= 0) {
			errno = ETIMEDOUT;
			return -1;
		}
		result = poll(&descriptor, 1, (int)remaining);
		if (result < 0 && errno == EINTR)
			continue;
		if (result <= 0) {
			if (!result)
				errno = ETIMEDOUT;
			return -1;
		}
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
			errno = EIO;
			return -1;
		}
		done = writing ? write(fd, bytes, count) : read(fd, bytes, count);
		if (done < 0 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (done <= 0) {
			if (!done)
				errno = EIO;
			return -1;
		}
		bytes += done;
		count -= (size_t)done;
	}
	if (stopped) {
		errno = EINTR;
		return -1;
	}
	return 0;
}

static int command(int fd, uint16_t opcode, const uint8_t *payload, uint8_t length)
{
	uint8_t packet[259] = { 1, opcode & 0xff, opcode >> 8, length };
	int64_t deadline = monotonic_ms() + COMMAND_TIMEOUT_MS;

	if (length)
		memcpy(packet + 4, payload, length);
	if (transfer(fd, packet, length + 4, 1, deadline) < 0)
		goto failure;
	for (;;) {
		uint8_t header[3], event[255];
		uint16_t completed_opcode;
		uint8_t status;

		if (transfer(fd, header, sizeof(header), 0, deadline) < 0)
			goto failure;
		if (header[0] != 4) {
			errno = EPROTO;
			goto failure;
		}
		if (transfer(fd, event, header[2], 0, deadline) < 0)
			goto failure;
		if (header[1] == 0x0e && header[2] >= 4) {
			completed_opcode = event[1] | (event[2] << 8);
			status = event[3];
		} else if (header[1] == 0x0f && header[2] >= 4) {
			completed_opcode = event[2] | (event[3] << 8);
			status = event[0];
			/* A successful command-status event is not command completion. */
			if (!status)
				continue;
		} else {
			continue;
		}
		if (completed_opcode != opcode)
			continue;
		if (status) {
			fprintf(stderr, "HCI command 0x%04x rejected: status 0x%02x\n", opcode, status);
			errno = EPROTO;
			return -1;
		}
		return 0;
	}
failure:
	fprintf(stderr, "HCI command 0x%04x failed: %s\n", opcode, strerror(errno));
	return -1;
}

static int read_address(const char *path, uint8_t address[6])
{
	char line[64], extra;
	unsigned int octet[6];
	FILE *file = fopen(path, "r");
	int count;

	if (!file) {
		perror("open Bluetooth address");
		return -1;
	}
	if (!fgets(line, sizeof(line), file)) {
		fclose(file);
		fprintf(stderr, "Bluetooth address file is empty\n");
		return -1;
	}
	fclose(file);
	/* scanf's %x also accepts signs and short octets; require canonical MAC
	 * syntax before conversion so malformed input cannot silently become FF. */
	if (strlen(line) < 17)
		goto invalid;
	for (int index = 0; index < 17; index++) {
		if (index % 3 == 2 ? line[index] != ':' : !isxdigit((unsigned char)line[index]))
			goto invalid;
	}
	count = sscanf(line, "%2x:%2x:%2x:%2x:%2x:%2x %c", &octet[0], &octet[1],
		       &octet[2], &octet[3], &octet[4], &octet[5], &extra);
	/* Match sunxi-addr's Bluetooth validation: unlike Wi-Fi, addr_bt is
	 * generated without masking the low bits of the first octet.
	 */
	if (count != 6 ||
	    !(octet[0] | octet[1] | octet[2] | octet[3] | octet[4] | octet[5]) ||
	    (octet[0] & octet[1] & octet[2] & octet[3] & octet[4] & octet[5]) == 0xff) {
		goto invalid;
	}
	for (int index = 0; index < 6; index++)
		address[5 - index] = (uint8_t)octet[index];
	return 0;
invalid:
	fprintf(stderr, "Invalid board Bluetooth address in %s\n", path);
	return -1;
}

static int initialize(int fd, const uint8_t address[6])
{
	/* Explicit little-endian wire image, independent of host struct padding. */
	static const uint8_t aon_parameters[104] = {
		0x00,0xd7,0x18,0x00, 0x00,0xf7,0x18,0x00, 0x40,0x00,0x00,0x00, 0x28,0x00,0x90,0x01,
		0x90,0x01,0x03,0x00, 0x02,0x00,0x03,0x00, 0x02,0x00,0x28,0x00, 0x00,0x02,0x14,0x00,
		0x15,0x00,0x14,0x00, 0x20,0x00,0x00,0x00, 0x08,0x00,0x00,0x00, 0xff,0xff,0xff,0xff,
		0x00,0x00,0x00,0x00, 0x20,0x4e,0x00,0x00, 0x13,0x01,0x00,0x00, 0x02,0x73,0x06,0x20,
		0x07,0x00,0x40,0x00, 0x48,0x00,0x47,0x00, 0x20,0x00,0x00,0x02, 0xa4,0x01,0x64,0x00,
		0x64,0x00,0x08,0x00, 0x18,0x00,0x28,0x00, 0x8c,0x00,0x00,0x00, 0x40,0x00,0x00,0x00,
		0x20,0x4e,0x00,0x00, 0x32,0x00,0x00,0x00
	};
	const uint8_t active = 0, slave = 1, power_off = 0;

	/* Match the image's LPM_ENABLE=0 and continuously asserted BT_WAKE. */
	return command(fd, 0x0c03, NULL, 0) ||
	       command(fd, 0xfc70, address, 6) ||
	       command(fd, 0xfc4d, aon_parameters, sizeof(aon_parameters)) ||
	       command(fd, 0xfc50, &active, 1) ||
	       command(fd, 0xfc51, &slave, 1) ||
	       command(fd, 0xfc52, &power_off, 1) ? -1 : 0;
}

int main(int argc, char **argv)
{
	struct termios settings, original;
	struct sigaction action = { .sa_handler = stop_handler };
	uint8_t address[6];
	sigset_t blocked, previous;
	int fd, old_discipline, discipline = N_HCI;
	int result = EXIT_FAILURE, discipline_changed = 0;

	if (argc != 3) {
		fprintf(stderr, "Usage: %s UART ADDRESS_FILE\n", argv[0]);
		return EXIT_FAILURE;
	}
	if (read_address(argv[2], address) < 0)
		return EXIT_FAILURE;
	sigemptyset(&action.sa_mask);
	sigaction(SIGTERM, &action, NULL);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGHUP, &action, NULL);
	fd = open(argv[1], O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0) {
		perror("open Bluetooth UART");
		return EXIT_FAILURE;
	}
	if (tcgetattr(fd, &original) < 0 || ioctl(fd, TIOCGETD, &old_discipline) < 0) {
		perror("read UART settings");
		goto close_uart;
	}
	if (old_discipline != N_TTY) {
		fprintf(stderr, "UART already has a non-terminal line discipline (%d)\n", old_discipline);
		goto close_uart;
	}
	if (ioctl(fd, TIOCEXCL) < 0) {
		perror("claim UART");
		goto close_uart;
	}
	settings = original;
	cfmakeraw(&settings);
	settings.c_cflag |= CLOCAL | CREAD | CRTSCTS;
	if (cfsetispeed(&settings, B1500000) < 0 || cfsetospeed(&settings, B1500000) < 0 ||
	    tcsetattr(fd, TCSANOW, &settings) < 0 || tcflush(fd, TCIOFLUSH) < 0) {
		perror("configure 1500000 baud H4 UART");
		goto restore_uart;
	}
	if (initialize(fd, address) < 0)
		goto restore_uart;
	if (ioctl(fd, TIOCSETD, &discipline) < 0) {
		perror("set HCI line discipline");
		goto restore_uart;
	}
	discipline_changed = 1;
	if (ioctl(fd, HCIUARTSETPROTO, HCI_UART_H4) < 0) {
		perror("register H4 protocol");
		goto restore_uart;
	}
	fprintf(stderr, "Bluetooth UART attached on %s at 1500000 baud\n", argv[1]);
	sigemptyset(&blocked);
	sigaddset(&blocked, SIGTERM);
	sigaddset(&blocked, SIGINT);
	sigaddset(&blocked, SIGHUP);
	sigprocmask(SIG_BLOCK, &blocked, &previous);
	while (!stopped)
		sigsuspend(&previous);
	sigprocmask(SIG_SETMASK, &previous, NULL);
	result = stopped == SIGHUP ? EXIT_FAILURE : EXIT_SUCCESS;

restore_uart:
	if (discipline_changed && ioctl(fd, TIOCSETD, &old_discipline) < 0)
		perror("restore UART line discipline");
	if (tcsetattr(fd, TCSANOW, &original) < 0)
		perror("restore UART settings");
	ioctl(fd, TIOCNXCL);
close_uart:
	close(fd);
	return result;
}
