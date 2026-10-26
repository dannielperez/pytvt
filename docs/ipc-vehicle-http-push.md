# IPC vehicle HTTP push evidence

Live IPC 1.7 VEHICLE payloads from IP-5IR4A3H4-MZ-LR firmware
5.2.2.7101B241021.IC5.U1(13A10) can append bytes after JPEG EOI in
both the overview and plate-crop base64 buffers. A complete live example had
one extra byte on each image. Rejecting a JPEG solely because EOI is not the
last buffer byte rejects valid vehicle observations.

The decoder keeps the existing decoded-size limit and JPEG SOI/EOI checks,
and returns only the image through its last EOI marker. It does not retain
transport trailers. Complete image integrity remains the consumer's job;
framing alone must not be treated as image validation or authorization.

Synthetic regression fixtures contain no real plates or images. Validation:
51 tests in test_vehicle_push.py and test_plate_events.py passed. Live capture
accepted approach direction, confidence and capture timestamp after this fix.
Camera clock synchronization must be independently checked; receipt time is
never substituted for capture time.

The subscription also sends alarmStatusInfo/vehiceAlarm boolean status frames.
These decode to no observations so consumers can acknowledge them without
inventing an arrival. Mixed status/untyped detection envelopes remain rejected.
