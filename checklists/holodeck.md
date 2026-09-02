# Holodeck — Voice-driven WebXR Gaussian-splat world viewer (functional, NO GPU gen)

Tests everything that does NOT require a fresh FlashWorld generation, using the
worlds that already exist on disk. The FlashWorld GPU service is intentionally
offline (shared 4090); cache-miss generations correctly resolve to the
`service_offline` error card — that IS the error-state test, not a failure.

IMPORTANT runtime: the splat canvas needs real WebGL. Headless Chromium uses
SwiftShader (blank canvas). Run headed under xvfb with
`--use-angle=vulkan` + `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json`
(local RTX 3060 — NOT the FIRMAMENT 4090). Debug hooks: `window.__hdStore`
(zustand store), `window.__hdCam` (live camera).

## App load
- [ ] App loads over http://localhost:3556/ with no console/page errors
- [ ] `window.isSecureContext` true over https://localhost:3557/ (proxy up) — WebXR-eligible
- [ ] WebGL renderer is real (ANGLE/Vulkan/NVIDIA), NOT SwiftShader/llvmpipe/software
- [ ] A splat `<canvas>` is present
- [ ] Boot sample.splat renders: store `voiceDebug.pipelineStage === 'world-loaded'`

## Gallery (honest index)
- [ ] `GET /worlds` returns 7 entries (6 distinct 616MB PLYs + sample.splat)
- [ ] No duplicate `url` values across entries; no phantom/mislabelled prompts
- [ ] `[data-testid=gallery-item]` count === 7
- [ ] Clicking a gallery item streams + renders that world (`world-loaded`)
- [ ] Loaded splat shows non-blank center pixels (real terrain, not black)

## Orbit / free-look (flat mode)
- [ ] Elevated establishing spawn pose (`__hdCam.position.y > 1.6`) on world load
- [ ] Drag on the canvas orbits the camera (`__hdCam.position` moves > 0.1)
- [ ] Enter VR button is disabled when no WebXR runtime (no crash on XR path)

## Loading bar phases
- [ ] On a streamed gallery load the loading bar enters `downloading` phase
- [ ] Download bar shows real byte-% (loadedBytes > 0, totalBytes === known file size)
- [ ] Phase progresses downloading -> decoding -> ready (auto-dismiss)

## Presets + Surprise me
- [ ] At least 4 preset chips (`[data-testid=preset]`) populate the controls
- [ ] `[data-testid=surprise-me]` chip exists; clicking it sets the prompt input

## Image upload (request shape — gen will error offline)
- [ ] Selecting an image file POSTs `/world/generate` with `image_prompt` data:image dataURI
- [ ] With FlashWorld offline the image gen resolves to the error card (no hang)

## Error states (FlashWorld offline)
- [ ] Typing a prompt + Generate -> job error -> error card appears (no hang)
- [ ] Error card shows a human message + machine `code: service_offline`
- [ ] Error card has Retry + Dismiss; Dismiss closes the card
- [ ] Curl: cache-miss POST /world/generate -> poll job -> `status:error reason:service_offline`

## Validation (400s, curl)
- [ ] Empty text_prompt -> 400 "text_prompt is empty"
- [ ] >500-char text_prompt -> 400 "text_prompt too long"
- [ ] params.n_frame=99 -> 400; params.resolution=[1,2] -> 400
- [ ] image_prompt > 8MB decoded -> 400 "image_prompt too large"

## Back stack
- [ ] Load a 2nd world; `canGoBack` true; worldUrl differs from the first
- [ ] Click Back -> restores the previous world's url (`world-loaded`)

## Responsive / a11y
- [ ] No horizontal overflow at 414px and 768px widths
- [ ] Interactive elements show a focus affordance (outline / box-shadow ring)

## Static / streaming headers
- [ ] `GET /worlds/<file>` with Range -> 206 Partial Content, Content-Range, Accept-Ranges
- [ ] Immutable cache header `public, max-age=31536000, immutable`
- [ ] `GET /worlds` gzipped with `Accept-Encoding: gzip`

## NEEDS GPU (correctly skipped here)
- [ ] Fresh text/image generation happy-path (generate -> poll -> ready -> render)
  requires the warm FlashWorld service on the 4090 — DEFERRED while the GPU is busy.
