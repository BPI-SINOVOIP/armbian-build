<div class="cover" markdown="1">
<div class="cover-accent"></div>
<div class="cover-grid"></div>
<div class="cover-content" markdown="1">
<p class="cover-kicker">CUSTOMER ENGINEERING REPORT</p>

# Banana Pi M4Zero A1<br>Porting Correction and DDR Tuning

<p class="cover-subtitle">A focused 792 MHz correction, observed margin evidence, standard image delivery, and the path to qualification</p>

<div class="status-chip">STATUS | ENGINEERING VALIDATION CANDIDATE</div>

| Document control | Value |
| --- | --- |
| Document revision | 1.0 |
| Report date | 20 August 2026 |
| Target platform | Banana Pi M4Zero A1 |
| DDR target | 792 MHz |
| Standard customer validation build | `P02e5` |
| Intended audience | Customers, technical partners, product managers, and customer engineering teams |

<p class="cover-note">This report summarizes verified repository evidence available at the document date. It defines an engineering validation candidate. It does not constitute production qualification or a stable release.</p>
</div>
</div>

<section class="report-page executive" markdown="1">

<p class="section-kicker">DECISION BRIEF</p>

# Executive Summary

## Outcome

The Banana Pi M4Zero A1 correction retains the 792 MHz DDR target and the established `TPR6=0x3a808080` center setting. It makes a minimal paired lane update: `TPR11` changes from `0x24242422` to `0x25252523`, and `TPR12` changes from `0x110f1111` to `0x110f0f10`.

The correction was required because the previous 792 MHz lane parameter pairing showed insufficient margin on an additional hardware sample under the stronger M2 data-integrity test. One of five repetitions produced a data mismatch. This evidence identifies a margin limitation in that parameter pairing under the tested condition. It does not support blaming the root filesystem, and it does not establish a unique physical root cause.

## Verified results at a glance

| Area | Verified result | Customer interpretation |
| --- | --- | --- |
| Parameter correction | 792 MHz and `TPR6=0x3a808080` retained; `TPR11` and `TPR12` updated as a pair | The change is narrow and traceable |
| Observed `TPR6` window | Upper-byte passing window `0x30..0x42`; deliberate failure boundaries `0x2e` and `0x44` | Useful engineering margin evidence, not a production tolerance guarantee |
| A1 center candidate | 64 MiB M2 five-pass pattern testing `20/20`; safe recovery `20/20` | No failure was observed in this defined validation sequence |
| Evidence dataset | 322 records: 270 passes and 52 retained convergence or boundary negative results | The 52 negative results are not an A1 candidate failure rate |
| Standard Linux boot | Covered 2 GiB single-rank and 4 GiB dual-rank configurations | Boot-chain and memory-geometry coverage, not long-duration qualification |
| Image delivery | 10 images across five distributions, each in CLI and XFCE variants | Checksums and image plus bootloader package identity were verified |

<div class="status-panel"><strong>Status statement.</strong> A1 at 792 MHz is an engineering validation candidate. It is not production-qualified and is not a stable release.</div>

## Recommended decision

Use standard build `P02e5` for controlled customer evaluation while the remaining qualification gates are completed. Keep experimental runtime scanning binaries separate from the standard bootloader throughout preparation, test, and feedback collection.

</section>

<section class="report-page" markdown="1">

<p class="section-kicker">CORRECTION RATIONALE</p>

# 1. Why the A1 Correction Was Needed

## Stronger testing exposed a real margin limitation

Earlier checks did not expose a repeatable data-integrity problem at the target frequency. The stronger M2 method expanded pattern and address coverage, including coverage intended to exercise more demanding memory regions. Under that method, the previous 792 MHz lane pairing completed four of five repetitions and produced one explicit data mismatch.

That result changed the engineering interpretation. A boot symptom alone can have several causes, but a data mismatch under a controlled DDR test is direct evidence that the previous lane pairing did not provide sufficient observed margin on the additional sample. The available data supports a parameter-level correction. It does not isolate memory silicon, PCB routing, power delivery, temperature, or any other physical factor as the sole cause.

## Minimal correction strategy

| Parameter | Previous 792 MHz setting | A1 setting | Decision |
| --- | --- | --- | --- |
| DDR frequency | `792 MHz` | `792 MHz` | Retained |
| `TPR6` | `0x3a808080` | `0x3a808080` | Retained at the observed center candidate |
| `TPR11` | `0x24242422` | `0x25252523` | Updated |
| `TPR12` | `0x110f1111` | `0x110f0f10` | Updated as a pair with `TPR11` |

The paired update was chosen because `TPR11` and `TPR12` encode related lane settings. Short comparison runs showed that changing `TPR11` produced the larger observed improvement, while changing only `TPR12` did not. The final engineering choice nevertheless updates and validates the two packed parameters together, avoiding an unsupported split configuration.

## What remained intentionally unchanged

The frequency target, drive settings, termination settings, `TPR6`, and `TPR10` were not changed. Preserving those established values limited the number of variables and kept the correction focused on the parameter pair implicated by the additional sample evidence.

<div class="insight-panel"><strong>Engineering principle.</strong> The A1 correction is a controlled lane-parameter adjustment at the existing performance target, not a broad retuning of the DDR configuration.</div>

</section>

<section class="report-page" markdown="1">

<p class="section-kicker">MARGIN AND CONVERGENCE</p>

# 2. DDR Window and Validation Evidence

## Observed `TPR6` upper-byte window

After applying the A1 lane pair, discrete M2 scans were used to observe the `TPR6` upper-byte envelope around the retained center candidate.

| Region or point | Observed result | Meaning |
| --- | ---: | --- |
| `0x2e` | Deliberate failure boundary | Nearest demonstrated lower-side failure point |
| `0x30..0x42` | Passing window at the tested points | Observed zero-failure engineering window |
| `0x3a` | Selected center candidate | Retained value in `TPR6=0x3a808080` |
| `0x44` | Deliberate failure boundary | Nearest demonstrated upper-side failure point |

The `0x30..0x42` result is an observed engineering window from discrete points under the recorded test conditions. It is not a production tolerance guarantee, and it must not be extrapolated across all units, batches, temperatures, or supply conditions.

## Center-candidate strength

The A1 center candidate completed a 64 MiB M2 sequence using a five-pass pattern test. The recorded outcome was:

| Validation item | Result | Scope |
| --- | ---: | --- |
| 64 MiB M2 five-pass pattern repetitions | `20/20` | A1 center candidate at 792 MHz |
| Return to the 480 MHz safe recovery setting | `20/20` | Recovery after each center-candidate sequence |

These results demonstrate repeatability within the defined warm-reset engineering sequence. They do not replace complete power-cycle testing or extended Linux stress.

## Correct interpretation of the 322-record dataset

| Dataset class | Records | Interpretation |
| --- | ---: | --- |
| Pass | 270 | Passing records from coarse scans, pair comparisons, window work, and center validation |
| Retained negative result | 52 | Convergence evidence, including old-setting counter-evidence and deliberate boundary searches |
| Total | 322 | Full retained engineering dataset |

The 52 retained negative results are essential to showing where settings stopped passing. They combine intentionally weak points, boundary exploration, and pre-correction evidence. Therefore, `52/322` is not an A1 center-candidate failure rate. The direct center-candidate result is the separate `20/20` result above.

</section>

<section class="report-page" markdown="1">

<p class="section-kicker">STANDARD DELIVERY COVERAGE</p>

# 3. Standard Boot and Image Matrix

## Customer validation build identity

`P02e5` is the standard customer validation build for A1 evaluation. It contains the fixed A1 DDR parameters and the standard boot path. A separate internal runtime scanning build was used to vary parameters during engineering convergence. That scanner is not the customer validation bootloader and must not be mixed into standard image testing.

| Build role | Intended use | Customer action |
| --- | --- | --- |
| Standard build `P02e5` | Normal A1 boot and controlled customer evaluation | Use this build and record it in every test result |
| Internal runtime scanning build | Engineering-only parameter exploration | Do not combine it with the standard bootloader or customer image results |

## Standard Linux boot coverage

Standard Linux boot records cover both represented memory geometry classes in the available sample set. The records include one 2 GiB single-rank sample and three 4 GiB dual-rank samples.

| Memory configuration | Verified coverage | Limit |
| --- | --- | --- |
| 2 GiB single-rank | Geometry detection, standard boot chain, Linux user space, and orderly shutdown | Not a cold-boot count or long-duration stress result |
| 4 GiB dual-rank | Geometry detection, standard boot chain, Linux user space, and orderly shutdown | Not a batch or environmental qualification result |

The records demonstrate that the same A1 parameter set reached standard Linux user space across both memory configurations. The source boot evidence used a Noble user space. It did not bind every boot record to a specific CLI or XFCE image file.

## Ten-image delivery matrix

| Distribution | CLI | XFCE |
| --- | :---: | :---: |
| Bookworm | Included | Included |
| Jammy | Included | Included |
| Noble | Included | Included |
| Resolute | Included | Included |
| Trixie | Included | Included |

For all 10 delivered image variants, the retained verification established compressed XZ checksums, decompressed raw IMG checksums, embedded `P02e5` identity, and bootloader-region identity. The standard bootloader package identity was also verified. These checks confirm delivery integrity and traceability. They do not mean that all 10 images completed full hardware stress qualification.

<div class="insight-panel"><strong>Customer checkpoint.</strong> Preserve the supplied checksum manifests with each image. After writing media, perform the readback gate before treating a file checksum as proof of the flashed content.</div>

</section>

<section class="report-page" markdown="1">

<p class="section-kicker">CLAIMS AND QUALIFICATION</p>

# 4. What the Evidence Demonstrates

## Claims and limitations

| Evidence-backed claim | What is demonstrated | What is not demonstrated |
| --- | --- | --- |
| A1 parameter correction | The defined engineering sequence passed after a narrow paired lane update | A unique physical root cause |
| Observed `TPR6` window | Passing points from `0x30..0x42` with failures at `0x2e` and `0x44` | A production tolerance, batch-wide, or environmental guarantee |
| Center validation | 64 MiB M2 five-pass pattern testing `20/20` and safe recovery `20/20` | Lifetime reliability, controlled cold-boot statistics, or extended operating stress |
| Memory-geometry boot coverage | Standard Linux boot on 2 GiB single-rank and 4 GiB dual-rank configurations | Broader lot coverage or all operating corners |
| Ten-image matrix | Image checksums, embedded build identity, and bootloader package identity for five distributions in CLI and XFCE forms | Full hardware stress completion for every image |

## Remaining qualification gates

| Gate | Required work | Completion evidence |
| --- | --- | --- |
| Controlled complete power-cycle cold boot | Run frozen off-time and on-time sequences on representative memory configurations | Complete per-cycle UART records and pass/fail accounting |
| Flashed-media and bootloader readback | Verify the written image range and the bootloader region before first boot | Readback hashes matched to the exact supplied image |
| Long-duration concurrent stress | Run Linux memory testing together with CPU and storage load for a predefined duration | Commands, duration, load, temperature, exit status, and error review |
| Broader common-window coverage | Expand across batches, memory geometries, weaker units, temperature, and supply corners | A shared passing intersection with predefined acceptance rules |

All four gates remain open. Until they are closed with controlled evidence, A1 remains an engineering validation candidate.

<div class="risk-panel"><strong>Qualification boundary.</strong> Standard boot success and a strong center-candidate result are necessary evidence, but they are not substitutes for cold boot, media readback, concurrent stress, and broader common-window coverage.</div>

</section>

<section class="report-page" markdown="1">

<p class="section-kicker">CUSTOMER EVALUATION</p>

# 5. Evaluation and Feedback Guidance

## Recommended evaluation sequence

1. Use the standard customer validation build `P02e5` without substituting an experimental scanner binary.
2. Preserve the original compressed-image checksum and the decompressed raw-image checksum with the test record.
3. Record the board memory geometry, including capacity and single-rank or dual-rank detection.
4. Write the selected image, then complete flashed-media and bootloader readback before the first validation boot.
5. Capture the complete UART log from initial power application through Linux shutdown or the point of failure.
6. Record the exact image identity: distribution, CLI or XFCE variant, full filename, and supplied checksum.
7. Keep power-cycle conditions, test commands, duration, and acceptance criteria fixed within a comparison set.

## Feedback package

For efficient joint analysis, each customer result should include:

| Field | Required detail |
| --- | --- |
| Build | `P02e5` |
| Image | Exact distribution, variant, filename, and checksum |
| Memory | Reported capacity and rank geometry |
| Media | Write method and readback checksum result |
| Power sequence | Controlled off-time, on-time, and cycle number |
| Test | Exact command, duration, load combination, and exit status |
| Logs | Complete UART output, including the earliest boot text and any failure context |
| Environment | Supply setup and available temperature information |

Do not merge scanner output with standard boot results. If experimental runtime scanning is required, treat it as a separate engineering activity with separately identified binaries and records.

## Conclusion and recommended next steps

The A1 correction is technically credible, deliberately small, and supported by observed margin boundaries, a `20/20` center-candidate result, standard boot coverage across both memory geometries, and a traceable ten-image delivery matrix. The appropriate next step is controlled customer validation with `P02e5`, beginning with power-cycle cold boot and media readback, followed by concurrent Linux stress and broader common-window work.

<div class="status-panel"><strong>Final status.</strong> Proceed with controlled engineering evaluation. Do not promote A1 beyond engineering validation candidate status until the remaining qualification gates are complete.</div>

</section>

<section class="report-page appendix" markdown="1">

<p class="section-kicker">TECHNICAL REFERENCE</p>

# Appendix A. Parameter Delta and Validation Summary

## A.1 Parameter delta

| Item | Previous setting | A1 setting | Change status |
| --- | --- | --- | --- |
| DDR clock | `792 MHz` | `792 MHz` | Retained |
| `TPR6` | `0x3a808080` | `0x3a808080` | Retained |
| `TPR11` | `0x24242422` | `0x25252523` | Updated |
| `TPR12` | `0x110f1111` | `0x110f0f10` | Updated |

## A.2 Validation summary

| Validation item | Verified result | Qualification note |
| --- | ---: | --- |
| Previous lane pair at 792 MHz under M2 | 4/5, including one data mismatch | Established insufficient observed margin on the additional sample |
| A1 observed passing window | `0x30..0x42` | Discrete engineering window only |
| Deliberate failure boundaries | `0x2e` and `0x44` | Demonstrated lower-side and upper-side failures |
| A1 center candidate | 64 MiB M2 five-pass pattern `20/20` | Defined warm-reset engineering sequence |
| Safe recovery | `20/20` | Returned to 480 MHz safe setting after each sequence |
| Full convergence dataset | 322 records: 270 pass, 52 retained negative | Negative results are not an A1 candidate failure rate |
| Standard boot geometry | 2 GiB single-rank and 4 GiB dual-rank | Standard Linux boot coverage only |
| Image matrix | 10/10 identities verified | Five distributions, each CLI and XFCE; hardware stress remains selective |

## A.3 Document control

| Control field | Value |
| --- | --- |
| Revision | 1.0 |
| Date | 20 August 2026 |
| Standard customer validation build | `P02e5` |
| Status | Engineering validation candidate |
| Source basis | Verified engineering report and repository evidence available at the report date |

This document is intentionally concise. Detailed internal scan logs, hardware identifiers, workstation locations, and development history are excluded. The stated results should be interpreted only within the scopes and limitations shown in this report.

</section>
