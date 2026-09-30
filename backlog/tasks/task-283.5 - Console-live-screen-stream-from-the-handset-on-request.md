---
id: TASK-283.5
title: 'Console: live screen stream from the handset on request'
status: To Do
assignee: []
created_date: '2026-09-23 16:24'
labels: []
dependencies:
  - TASK-283.3
parent_task_id: TASK-283
priority: medium
project: whatsapp
ordinal: 235000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: "было бы вообще в идеале, было бы прикольно по запросу получать онлайн-стрим прям с экрана. Вот. Но если уже не онлайн-стрим, то хотя бы какую-то визуализацию того, что у нас попадает в базу данных." He named it as a wish with a stated fallback, so the fallback is the floor and the stream is the reach.

What already exists to build on: bridge/adb_driver.py can screenshot (adb exec-out screencap) and can
record (adb shell screenrecord, with a hard ~180 s per-invocation Android ceiling that is an OS limit
and not ours). The debug capture already writes per-op screenshots and an mp4 into shots/ and
recordings/ on the mini, named by op_id, kept 14 days and reviewed before deletion (bridge/retention.py).
So the cheap version of "see the screen" is already on disk and just needs serving.

The real constraint on a LIVE stream is the phone lock, not the video. One handset, one flock, and a
stream that holds it starves the conversations. Whatever this becomes has to either take its turn in
the phone-ops queue like everything else (TASK-283.3) or read without the lock and say plainly that
what it shows may be a moment behind. Deciding which is the substance of this task.

The fallback Ivan named -- visualising what lands in the database -- is largely the other subtasks
under TASK-283; what belongs HERE instead is serving the debug-capture artefacts that already exist,
so a person can see the screens of a finished operation even when no live view is possible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The per-op screenshots and recording already captured on the mini are viewable from the console, joined to the op that produced them
- [ ] #2 A live view, if built, never starves a real conversation of the handset, and the mechanism by which it cannot is stated
- [ ] #3 If a live view is not built, the task records why and the artefact view stands on its own
- [ ] #4 Nothing here becomes a second way to drive the phone
<!-- AC:END -->
