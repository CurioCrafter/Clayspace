/* Focused stream-header trust-boundary check; no camera or Blender process. */
#include "../native/blender-5.1.2/source/blender/windowmanager/xr/intern/wm_xr_camera_stream.hh"

#include <array>
#include <cassert>
#include <cstdint>

int main()
{
  std::array<uint32_t, 6> header{0x42575258u, 1u, 960u, 960u, 2u, 2u};
  assert(blender::xr_camera::valid_header(header));
  assert(blender::xr_camera::stereo_bytes == 7372800u);
  assert(blender::xr_camera::fresh_capture(500u, 500u));
  assert(blender::xr_camera::fresh_capture(500u, 500u + blender::xr_camera::max_capture_age_ns));
  assert(!blender::xr_camera::fresh_capture(500u, 499u));
  assert(!blender::xr_camera::fresh_capture(500u, 501u + blender::xr_camera::max_capture_age_ns));
  for (size_t field = 0; field < header.size(); field++) {
    const uint32_t saved = header[field];
    header[field] = field == 4 || field == 5 ? 5u : 0u;
    assert(!blender::xr_camera::valid_header(header));
    header[field] = saved;
  }
  std::array<unsigned char, blender::xr_camera::packet_header_bytes> packet{};
  const auto put_u32 = [&](size_t offset, uint32_t value) {
    for (size_t i = 0; i < 4; i++) { packet[offset + i] = (value >> (i * 8)) & 0xff; }
  };
  const auto put_u64 = [&](size_t offset, uint64_t value) {
    put_u32(offset, uint32_t(value));
    put_u32(offset + 4, uint32_t(value >> 32));
  };
  put_u32(0, 0x4D575258u);
  put_u32(4, 2);
  put_u32(8, 80);
  put_u32(12, 960);
  put_u32(16, 960);
  put_u32(20, 17);
  put_u32(24, 7);
  put_u32(28, 3686400);
  put_u32(32, 1);
  put_u32(36, 1);
  put_u32(40, 1);
  put_u32(56, 42);
  put_u64(64, 5000000000ULL);
  put_u64(72, 5042000000ULL);
  assert(blender::xr_camera::valid_packet_header(packet));
  put_u32(32, 5);
  assert(!blender::xr_camera::valid_packet_header(packet));
  put_u32(32, 1);
  put_u32(56, 41);
  assert(!blender::xr_camera::valid_packet_header(packet));
  put_u32(56, 42);
  put_u64(72, 4999999999ULL);
  assert(!blender::xr_camera::valid_packet_header(packet));
  return 0;
}
