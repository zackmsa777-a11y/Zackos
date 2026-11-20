/* Early userspace animation on a Linux KMS dumb buffer. No X server or GPU
 * acceleration is needed. The display service signals us before starting X. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <math.h>
#include <png.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#define PAPER 0x10191c
#define PELLET 0xd8ff62
#define IVORY 0xf8f9ee

struct screen {
    int fd, width, height, stride;
    uint32_t connector, crtc, fb;
    struct drm_mode_create_dumb buffer;
    drmModeCrtc *previous;
    uint32_t *pixels;
    double scale;
};

static volatile sig_atomic_t stopped;

static void stop(int sig) {
    (void)sig;
    stopped = 1;
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static uint32_t choose_crtc(int fd, drmModeRes *res, drmModeConnector *port) {
    for (int i = 0; i < port->count_encoders; i++) {
        drmModeEncoder *encoder = drmModeGetEncoder(fd, port->encoders[i]);
        if (!encoder) continue;
        uint32_t id = encoder->crtc_id;
        for (int j = 0; !id && j < res->count_crtcs && j < 32; j++)
            if (encoder->possible_crtcs & (1u << j)) id = res->crtcs[j];
        drmModeFreeEncoder(encoder);
        if (id) return id;
    }
    return 0;
}

static bool select_output(struct screen *s, drmModeModeInfo *mode) {
    drmModeRes *res = drmModeGetResources(s->fd);
    if (!res) return false;
    for (int i = 0; !s->crtc && i < res->count_connectors; i++) {
        drmModeConnector *port = drmModeGetConnector(s->fd, res->connectors[i]);
        if (!port) continue;
        if (port->connection == DRM_MODE_CONNECTED && port->count_modes) {
            s->crtc = choose_crtc(s->fd, res, port);
            s->connector = port->connector_id;
            *mode = port->modes[0];
            for (int j = 0; j < port->count_modes; j++) {
                if (port->modes[j].type & DRM_MODE_TYPE_PREFERRED) {
                    *mode = port->modes[j];
                    break;
                }
            }
        }
        drmModeFreeConnector(port);
    }
    drmModeFreeResources(res);
    return s->crtc != 0;
}

static void paint_logo(struct screen *s, const png_image *logo, const uint8_t *rgb) {
    s->scale = fmin((double)s->width / logo->width, (double)s->height / logo->height);
    int w = (int)(logo->width * s->scale);
    int h = (int)(logo->height * s->scale);
    int left = (s->width - w) / 2, top = (s->height - h) / 2;
    for (int y = 0; y < s->height; y++) {
        uint32_t *row = s->pixels + (size_t)y * s->stride;
        for (int x = 0; x < s->width; x++) row[x] = PAPER;
        if (y < top || y >= top + h) continue;
        const uint8_t *src = rgb + 3u * logo->width * ((y - top) * logo->height / h);
        for (int x = 0; x < w; x++) {
            const uint8_t *p = src + 3u * (x * logo->width / w);
            row[left + x] = (p[0] << 16) | (p[1] << 8) | p[2];
        }
    }
}

static bool start_screen(struct screen *s, const png_image *logo, const uint8_t *rgb) {
    drmModeModeInfo mode;
    if (!select_output(s, &mode) || drmSetMaster(s->fd)) return false;
    s->previous = drmModeGetCrtc(s->fd, s->crtc);
    s->width = mode.hdisplay;
    s->height = mode.vdisplay;
    s->buffer.width = s->width;
    s->buffer.height = s->height;
    s->buffer.bpp = 32;
    if (drmIoctl(s->fd, DRM_IOCTL_MODE_CREATE_DUMB, &s->buffer)) return false;
    s->stride = s->buffer.pitch / sizeof(uint32_t);
    if (drmModeAddFB(s->fd, s->width, s->height, 24, 32, s->buffer.pitch,
                     s->buffer.handle, &s->fb)) return false;
    struct drm_mode_map_dumb map = {.handle = s->buffer.handle};
    if (drmIoctl(s->fd, DRM_IOCTL_MODE_MAP_DUMB, &map)) return false;
    s->pixels = mmap(NULL, s->buffer.size, PROT_READ | PROT_WRITE, MAP_SHARED, s->fd, map.offset);
    if (s->pixels == MAP_FAILED) return false;
    paint_logo(s, logo, rgb);
    if (drmModeSetCrtc(s->fd, s->crtc, s->fb, 0, 0, &s->connector, 1, &mode)) return false;
    drmModeDirtyFB(s->fd, s->fb, NULL, 0);
    return true;
}

static void animate(struct screen *s, double elapsed) {
    const double leg = 1.45;
    double t = fmod(elapsed, 2 * leg) / leg;
    int facing = t < 1.0 ? 1 : -1;
    double head = -110 + 220 * (t < 1.0 ? t : 2.0 - t);
    double mouth = 0.07 + 0.85 * (0.5 - 0.5 * cos(elapsed * 28));
    int cx = s->width / 2, cy = s->height / 2 + (int)(80 * s->scale);
    int half = (int)ceil(123 * s->scale), tall = (int)ceil(12 * s->scale);
    for (int py = cy - tall; py <= cy + tall; py++) {
        double y = (py - cy) / s->scale;
        uint32_t *row = s->pixels + (size_t)py * s->stride;
        for (int px = cx - half; px <= cx + half; px++) {
            double x = (px - cx) / s->scale;
            double dx = (x - head) * facing;
            uint32_t color = PAPER;
            double dot = round(x / 22.0) * 22.0;
            double offset = x - dot;
            if (fabs(dot) <= 88 && (dot - head) * facing > 0 && offset*offset + y*y < 6.25)
                color = PELLET;
            if (dx*dx + y*y <= 100 && (dx < 0 || fabs(y) > dx * mouth)) {
                color = IVORY;
                if ((dx+1)*(dx+1) + (y+5)*(y+5) < 1.8) color = PAPER;
            }
            row[px] = color;
        }
    }
    drmModeClip clip = {cx-half, cy-tall, cx+half+1, cy+tall+1};
    drmModeDirtyFB(s->fd, s->fb, &clip, 1);
}

static void close_screen(struct screen *s) {
    if (s->pixels && s->pixels != MAP_FAILED) munmap(s->pixels, s->buffer.size);
    if (s->fb) drmModeRmFB(s->fd, s->fb);
    if (s->previous) drmModeFreeCrtc(s->previous);
    close(s->fd); /* The last GEM handle, including partial setup, is released here. */
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    png_image logo = {.version = PNG_IMAGE_VERSION};
    if (!png_image_begin_read_from_file(&logo, argv[1])) return 1;
    if (logo.width != 800 || logo.height != 600) return 1;
    logo.format = PNG_FORMAT_RGB;
    uint8_t *rgb = malloc(PNG_IMAGE_SIZE(logo));
    if (!rgb || !png_image_finish_read(&logo, NULL, rgb, 0, NULL)) return 1;
    struct screen s;
    bool ready = false;
    double deadline = now() + 2;
    while (!ready && !stopped && now() < deadline) {
        for (int i = 0; i < 8 && !ready; i++) {
            char path[32];
            snprintf(path, sizeof(path), "/dev/dri/card%d", i);
            s = (struct screen){.fd = open(path, O_RDWR | O_CLOEXEC)};
            if (s.fd < 0) continue;
            ready = start_screen(&s, &logo, rgb);
            if (!ready) close_screen(&s);
        }
        if (!ready) usleep(50000);
    }
    free(rgb);
    png_image_free(&logo);
    if (!ready) return 0;
    double start = now();
    while (!stopped && now() - start < 60.0) {
        drmModeCrtc *current = drmModeGetCrtc(s.fd, s.crtc);
        bool replaced = !current || current->buffer_id != s.fb;
        if (current) drmModeFreeCrtc(current);
        if (replaced) break;
        animate(&s, now() - start);
        usleep(40000);
    }
    /* Restore the previous scanout when the kernel provided one. */
    if (drmSetMaster(s.fd) == 0) {
        drmModeCrtc *current = drmModeGetCrtc(s.fd, s.crtc);
        if (current && current->buffer_id == s.fb && s.previous && s.previous->mode_valid)
            drmModeSetCrtc(s.fd, s.crtc, s.previous->buffer_id, s.previous->x,
                           s.previous->y, &s.connector, 1, &s.previous->mode);
        if (current) drmModeFreeCrtc(current);
    }
    close_screen(&s);
    return 0;
}
