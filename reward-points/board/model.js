// Board model for the reward-points spike.
// Two statecharts in Harel & Politi notation, laid out in absolute canvas
// coordinates. Hand-synced with ../model/rewards.yaml (the triview model);
// this file adds only x/y/w/h and label nudges (lx/ly, bend).
//
// Node kinds: state | region (orthogonal component of an and-state) |
//             default (the default-entrance dot) | conn (C, T connector)
// Edge:       {from, to, label, bend?, lx?, ly?, self?}
//   bend  = perpendicular offset of the control point (px), for parallel edges
//   self  = draw as a loop on the node's top-right corner

window.CHARTS = {

  CARD_STATES: {
    title: "CARD_STATES — one instance per card (generic, ch. 14)",
    subtitle: "Controls TRACK_CARD's siblings; reacts to PURCHASE / REFUND / CREDIT_POSTED / BALANCE_TYPED and to ch(TODAY)",
    nodes: [
      { kind: "state", id: "CARD_STATES", name: "CARD_STATES", x: 20, y: 20, w: 1660, h: 1560 },
      { kind: "default", id: "d0", x: 70, y: 80, parent: "CARD_STATES" },
      { kind: "state", id: "INACTIVE", name: "INACTIVE", x: 1480, y: 120, w: 170, h: 56, parent: "CARD_STATES",
        desc: "inactive: true / status: closed" },
      { kind: "state", id: "ACTIVE", name: "ACTIVE", and: true, x: 40, y: 100, w: 1400, h: 1440, parent: "CARD_STATES" },

      // --- PERIOD: one instance per statement credit x calendar period ---
      { kind: "region", id: "R_PERIOD", name: "PERIOD  ‹generic: one per credit × period›", x: 50, y: 130, w: 1380, h: 200, parent: "ACTIVE" },
      { kind: "default", id: "d1", x: 90, y: 230, parent: "R_PERIOD" },
      { kind: "conn", id: "c1", c: "C", x: 150, y: 230, parent: "R_PERIOD" },
      { kind: "state", id: "NOT_COUNTED", name: "NOT_COUNTED", x: 290, y: 150, w: 240, h: 56, parent: "R_PERIOD",
        desc: "period ended before the benefit began" },
      { kind: "state", id: "FUTURE", name: "FUTURE", x: 290, y: 255, w: 240, h: 50, parent: "R_PERIOD" },
      { kind: "state", id: "OPEN", name: "OPEN", x: 580, y: 150, w: 380, h: 150, parent: "R_PERIOD",
        reactions: [
          "QUALIFYING_SPEND / SPEND := SPEND + AMT",
          "CREDIT_POSTED / POSTED := POSTED + AMT",
          "ch(SPEND) or ch(POSTED) /",
          "   USED_AMT := min(AMOUNT, max(SPEND, POSTED))",
          "ch(TODAY) / DAYS_LEFT := P_END - TODAY" ] },
      { kind: "conn", id: "c2", c: "C", x: 1040, y: 230, parent: "R_PERIOD" },
      { kind: "state", id: "USED", name: "USED", x: 1170, y: 145, w: 180, h: 46, parent: "R_PERIOD" },
      { kind: "state", id: "PARTIAL", name: "PARTIAL", x: 1170, y: 207, w: 180, h: 46, parent: "R_PERIOD" },
      { kind: "state", id: "MISSED", name: "MISSED", x: 1170, y: 269, w: 180, h: 46, parent: "R_PERIOD" },

      // --- ENROLLMENT: one per statement credit ---
      { kind: "region", id: "R_ENROLL", name: "ENROLLMENT  ‹generic: one per credit›", x: 50, y: 345, w: 1380, h: 115, parent: "ACTIVE" },
      { kind: "default", id: "d2", x: 90, y: 405, parent: "R_ENROLL" },
      { kind: "conn", id: "c3", c: "C", x: 150, y: 405, parent: "R_ENROLL" },
      { kind: "state", id: "NOT_ENROLLED", name: "NOT_ENROLLED", x: 320, y: 370, w: 310, h: 70, parent: "R_ENROLL",
        reactions: [ "ns / WARNINGS := WARNINGS & `not enrolled`" ] },
      { kind: "state", id: "ENROLLED", name: "ENROLLED", x: 800, y: 380, w: 320, h: 56, parent: "R_ENROLL",
        desc: "counted in CREDITS_USED and EXPIRING" },

      // --- EARN_CAP: one per earn rule ---
      { kind: "region", id: "R_EARN", name: "EARN_CAP  ‹generic: one per earn rule›", x: 50, y: 475, w: 1380, h: 170, parent: "ACTIVE" },
      { kind: "default", id: "d3", x: 150, y: 560, parent: "R_EARN" },
      { kind: "state", id: "UNDER_CAP", name: "UNDER_CAP", x: 230, y: 500, w: 470, h: 130, parent: "R_EARN",
        desc: "a rule with no cap never leaves",
        reactions: [
          "PURCHASE / $U := min(SPEND, CAP_AMOUNT - CAP_USED);",
          "   CAP_USED := CAP_USED + $U;",
          "   POINTS := POINTS + $U*RATE + (SPEND - $U)*BASE_RATE",
          "REFUND / POINTS := POINTS - REFUND_AMT*RATE" ] },
      { kind: "state", id: "OVER_CAP", name: "OVER_CAP", x: 920, y: 505, w: 400, h: 100, parent: "R_EARN",
        reactions: [
          "PURCHASE / POINTS := POINTS + SPEND*BASE_RATE",
          "REFUND / POINTS := POINTS - REFUND_AMT*RATE" ] },

      // --- SIGNUP_BONUS ---
      { kind: "region", id: "R_SUB", name: "SIGNUP_BONUS", x: 50, y: 660, w: 1380, h: 165, parent: "ACTIVE" },
      { kind: "default", id: "d4", x: 90, y: 745, parent: "R_SUB" },
      { kind: "conn", id: "c4", c: "C", x: 150, y: 745, parent: "R_SUB" },
      { kind: "state", id: "NO_BONUS", name: "NO_BONUS", x: 290, y: 722, w: 180, h: 46, parent: "R_SUB" },
      { kind: "state", id: "IN_PROGRESS", name: "IN_PROGRESS", x: 560, y: 690, w: 480, h: 110, parent: "R_SUB",
        reactions: [
          "PURCHASE[OPENED <= DATE and DATE <= DEADLINE] /",
          "   SUB_SPEND := SUB_SPEND + SPEND",
          "ch(TODAY) / DAILY_NEEDED :=",
          "   (SPEND_TARGET - SUB_SPEND) / (DEADLINE - TODAY)" ] },
      { kind: "state", id: "MET", name: "MET", x: 1170, y: 690, w: 180, h: 46, parent: "R_SUB" },
      { kind: "state", id: "EXPIRED", name: "EXPIRED", x: 1170, y: 762, w: 180, h: 46, parent: "R_SUB" },

      // --- FEE_CYCLE ---
      { kind: "region", id: "R_FEE", name: "FEE_CYCLE", x: 50, y: 840, w: 1380, h: 150, parent: "ACTIVE" },
      { kind: "default", id: "d5", x: 90, y: 925, parent: "R_FEE" },
      { kind: "conn", id: "c5", c: "C", x: 150, y: 925, parent: "R_FEE" },
      { kind: "state", id: "ANNIV_UNKNOWN", name: "ANNIV_UNKNOWN", x: 290, y: 902, w: 220, h: 46, parent: "R_FEE" },
      { kind: "state", id: "AWAITING_FEE", name: "AWAITING_FEE", x: 700, y: 890, w: 400, h: 70, parent: "R_FEE",
        reactions: [ "ch(TODAY) / DAYS_UNTIL := NEXT_ANNIV - TODAY" ] },

      // --- BALANCE_CHECK ---
      { kind: "region", id: "R_BAL", name: "BALANCE_CHECK", x: 50, y: 1005, w: 1380, h: 185, parent: "ACTIVE" },
      { kind: "default", id: "d6", x: 130, y: 1115, parent: "R_BAL" },
      { kind: "state", id: "NO_READING", name: "NO_READING", x: 200, y: 1092, w: 160, h: 46, parent: "R_BAL" },
      { kind: "state", id: "ONE_READING", name: "ONE_READING", x: 560, y: 1055, w: 360, h: 120, parent: "R_BAL",
        reactions: [
          "PURCHASE[DATE > AS_OF] /",
          "   EARNED_SINCE := EARNED_SINCE + PTS_EARNED",
          "ch(EARNED_SINCE) /",
          "   EXPECTED_NOW := REPORTED + EARNED_SINCE" ] },
      { kind: "state", id: "TWO_READINGS", name: "TWO_READINGS", x: 1000, y: 1055, w: 360, h: 120, parent: "R_BAL",
        reactions: [
          "PURCHASE[DATE > AS_OF] /",
          "   EARNED_SINCE := EARNED_SINCE + PTS_EARNED",
          "ch(EARNED_SINCE) /",
          "   EXPECTED_NOW := REPORTED + EARNED_SINCE" ] },

      // --- PROMO_RATE (debts.yaml promos:) ---
      { kind: "region", id: "R_PROMO", name: "PROMO_RATE  (debts.yaml › promos)", x: 50, y: 1205, w: 1380, h: 190, parent: "ACTIVE" },
      { kind: "default", id: "d7", x: 90, y: 1300, parent: "R_PROMO" },
      { kind: "conn", id: "c6", c: "C", x: 150, y: 1300, parent: "R_PROMO" },
      { kind: "state", id: "NO_PROMO", name: "NO_PROMO", x: 290, y: 1277, w: 180, h: 46, parent: "R_PROMO" },
      { kind: "state", id: "PROMO_OFFERED", name: "PROMO_OFFERED", x: 520, y: 1275, w: 180, h: 50, parent: "R_PROMO" },
      { kind: "state", id: "PROMO_ACTIVE", name: "PROMO_ACTIVE", x: 760, y: 1225, w: 480, h: 150, parent: "R_PROMO" },
      { kind: "default", id: "d8", x: 775, y: 1255, parent: "PROMO_ACTIVE" },
      { kind: "state", id: "ADDING_SPEND", name: "ADDING_SPEND", x: 780, y: 1280, w: 220, h: 80, parent: "PROMO_ACTIVE",
        reactions: [ "ch(TODAY) / ROOM :=", "   CREDIT_LIMIT*TARGET_UTIL - BAL" ] },
      { kind: "state", id: "STOP_ADDING", name: "STOP_ADDING", x: 1090, y: 1295, w: 130, h: 50, parent: "PROMO_ACTIVE" },
      { kind: "state", id: "STANDARD_RATE", name: "STANDARD_RATE", x: 1275, y: 1275, w: 150, h: 50, parent: "R_PROMO" },

      // --- PAYDOWN_ROLE (debt_plan.py) ---
      { kind: "region", id: "R_ROLE", name: "PAYDOWN_ROLE  (debt_plan.py)", x: 50, y: 1410, w: 1380, h: 115, parent: "ACTIVE" },
      { kind: "default", id: "d9", x: 90, y: 1470, parent: "R_ROLE" },
      { kind: "conn", id: "c7", c: "C", x: 150, y: 1470, parent: "R_ROLE" },
      { kind: "state", id: "PAID_IN_FULL", name: "PAID_IN_FULL", x: 330, y: 1445, w: 240, h: 50, parent: "R_ROLE",
        desc: "out of the avalanche" },
      { kind: "state", id: "CARRIED", name: "CARRIED", x: 800, y: 1445, w: 260, h: 50, parent: "R_ROLE",
        desc: "in the avalanche / snowball" }
    ],
    edges: [
      { from: "d0", to: "ACTIVE" },
      { from: "ACTIVE", to: "INACTIVE", label: "CLOSE_CARD", lx: 0, ly: -8 },

      // PERIOD
      { from: "d1", to: "c1" },
      { from: "c1", to: "NOT_COUNTED", label: "[P_END < BENEFIT_START]", lx: -4, ly: -10 },
      { from: "c1", to: "FUTURE", label: "[P_END >= BENEFIT_START]", lx: -4, ly: 16 },
      { from: "FUTURE", to: "OPEN", label: "tr(TODAY >= P_START)", lx: 0, ly: 14 },
      { from: "OPEN", to: "USED", label: "tr(USED_AMT >= AMOUNT)", lx: 0, ly: -8 },
      { from: "OPEN", to: "c2", label: "tr(TODAY > P_END)\n[USED_AMT < AMOUNT]", lx: 0, ly: 28 },
      { from: "c2", to: "PARTIAL", label: "[USED_AMT > 0]", lx: 0, ly: -6 },
      { from: "c2", to: "MISSED", label: "[USED_AMT = 0]", lx: 0, ly: 16 },

      // ENROLLMENT
      { from: "d2", to: "c3" },
      { from: "c3", to: "NOT_ENROLLED", label: "[not ENROLLED_FLAG]", lx: 0, ly: -10 },
      { from: "c3", to: "ENROLLED", label: "[ENROLLED_FLAG]", bend: 48, lx: 0, ly: 12 },
      { from: "NOT_ENROLLED", to: "ENROLLED", label: "ENROLL", lx: 0, ly: -8 },

      // EARN_CAP
      { from: "d3", to: "UNDER_CAP" },
      { from: "UNDER_CAP", to: "OVER_CAP", label: "tr(CAP_USED >= CAP_AMOUNT)", bend: -24, lx: 0, ly: -6 },
      { from: "OVER_CAP", to: "UNDER_CAP", label: "PERIOD_ROLLOVER / CAP_USED := 0", bend: -24, lx: 0, ly: 14 },

      // SIGNUP_BONUS
      { from: "d4", to: "c4" },
      { from: "c4", to: "NO_BONUS", label: "[not SUB_DEFINED]", lx: 0, ly: -10 },
      { from: "c4", to: "IN_PROGRESS", label: "[SUB_DEFINED]", bend: 40, lx: 0, ly: 12 },
      { from: "IN_PROGRESS", to: "MET", label: "tr(SUB_SPEND >= SPEND_TARGET)", lx: 0, ly: -8 },
      { from: "IN_PROGRESS", to: "EXPIRED", label: "tr(TODAY > DEADLINE)\n[SUB_SPEND < SPEND_TARGET]", lx: 15, ly: 26 },

      // FEE_CYCLE
      { from: "d5", to: "c5" },
      { from: "c5", to: "ANNIV_UNKNOWN", label: "[not ANNIV_KNOWN]", lx: 0, ly: -10 },
      { from: "c5", to: "AWAITING_FEE", label: "[ANNIV_KNOWN]", bend: 44, lx: 0, ly: 12 },
      { from: "ANNIV_UNKNOWN", to: "AWAITING_FEE", label: "FEE_POSTED /\nNEXT_ANNIV := FEE_DATE + 1Y", lx: 0, ly: -16 },
      { from: "AWAITING_FEE", to: "AWAITING_FEE", self: true, label: "tr(TODAY > NEXT_ANNIV) /\nNEXT_ANNIV := NEXT_ANNIV + 1Y", lx: 0, ly: 0 },

      // BALANCE_CHECK
      { from: "d6", to: "NO_READING" },
      { from: "NO_READING", to: "ONE_READING", label: "BALANCE_TYPED /\nAS_OF := DATE;\nREPORTED := PTS", lx: 0, ly: -24 },
      { from: "ONE_READING", to: "TWO_READINGS", label: "BALANCE_TYPED /\nDRIFT := (PTS - REPORTED) - EARNED_SINCE;\nREPORTED := PTS; AS_OF := DATE; EARNED_SINCE := 0", lx: 0, ly: -72 },
      { from: "TWO_READINGS", to: "TWO_READINGS", self: true, label: "BALANCE_TYPED / (same)", lx: 0, ly: 4 },

      // PROMO_RATE
      { from: "d7", to: "c6" },
      { from: "c6", to: "NO_PROMO", label: "[not PROMO_DEFINED]", lx: 0, ly: -10 },
      { from: "c6", to: "PROMO_OFFERED", label: "[PROMO_DEFINED and not ACTIVATED]", bend: 46, lx: 0, ly: 12 },
      { from: "c6", to: "PROMO_ACTIVE", label: "[PROMO_DEFINED and ACTIVATED]", bend: 84, lx: 0, ly: 12 },
      { from: "PROMO_OFFERED", to: "PROMO_ACTIVE", label: "ACTIVATE", lx: 0, ly: -8 },
      { from: "PROMO_OFFERED", to: "NO_PROMO", label: "tr(TODAY > ACTIVATE_BY)", bend: 40, lx: 0, ly: -14 },
      { from: "d8", to: "ADDING_SPEND" },
      { from: "ADDING_SPEND", to: "STOP_ADDING", label: "tr(TODAY >= STOP_DATE)", lx: 0, ly: -45 },
      { from: "PROMO_ACTIVE", to: "STANDARD_RATE", label: "tr(TODAY >= UNTIL) /\nAPR := STANDARD_APR", lx: 60, ly: -44 },

      // PAYDOWN_ROLE
      { from: "d9", to: "c7" },
      { from: "c7", to: "PAID_IN_FULL", label: "[AUTOPAY_FULL and\nnot CARRIED_FLAG]", lx: 0, ly: -16 },
      { from: "c7", to: "CARRIED", label: "[not AUTOPAY_FULL\nor CARRIED_FLAG]", bend: 44, lx: 0, ly: 18 },
      { from: "PAID_IN_FULL", to: "CARRIED", label: "en(PROMO_ACTIVE)", bend: -22, lx: 0, ly: -8 },
      { from: "CARRIED", to: "PAID_IN_FULL", label: "en(STANDARD_RATE)\n[AUTOPAY_FULL and not CARRIED_FLAG]", bend: -30, lx: 0, ly: 24 }
    ]
  },

  REWARDS_CONTROL: {
    title: "REWARDS_CONTROL — one run of rewards.py",
    subtitle: "Controls its siblings in REWARDS: FETCH_MONARCH, READ_SNAPSHOT, WRITE_SNAPSHOT, LOAD_RULES, TRACK_CARD, RENDER_REPORT",
    nodes: [
      { kind: "state", id: "REWARDS_CONTROL", name: "REWARDS_CONTROL", x: 20, y: 20, w: 1580, h: 580 },
      { kind: "default", id: "d0", x: 70, y: 150, parent: "REWARDS_CONTROL" },
      { kind: "conn", id: "c1", c: "C", x: 130, y: 150, parent: "REWARDS_CONTROL" },
      { kind: "state", id: "FETCHING", name: "FETCHING", x: 220, y: 60, w: 260, h: 80, parent: "REWARDS_CONTROL",
        act: "throughout FETCH_MONARCH" },
      { kind: "state", id: "READING_SNAPSHOT", name: "READING_SNAPSHOT", x: 220, y: 220, w: 260, h: 80, parent: "REWARDS_CONTROL",
        act: "throughout READ_SNAPSHOT" },
      { kind: "conn", id: "c2", c: "C", x: 560, y: 100, parent: "REWARDS_CONTROL" },
      { kind: "conn", id: "c3", c: "C", x: 560, y: 260, parent: "REWARDS_CONTROL" },
      { kind: "state", id: "WRITING_SNAPSHOT", name: "WRITING_SNAPSHOT", x: 640, y: 60, w: 260, h: 80, parent: "REWARDS_CONTROL",
        act: "throughout WRITE_SNAPSHOT" },
      { kind: "state", id: "LOADING_RULES", name: "LOADING_RULES", x: 960, y: 140, w: 260, h: 80, parent: "REWARDS_CONTROL",
        act: "throughout LOAD_RULES" },
      { kind: "conn", id: "c4", c: "C", x: 1290, y: 180, parent: "REWARDS_CONTROL" },
      { kind: "state", id: "ANALYZING", name: "ANALYZING", x: 1340, y: 60, w: 240, h: 90, parent: "REWARDS_CONTROL",
        act: "throughout TRACK_CARD", desc: "one TRACK_CARD per card (ch. 14)" },
      { kind: "state", id: "RENDERING", name: "RENDERING", x: 1340, y: 300, w: 240, h: 80, parent: "REWARDS_CONTROL",
        act: "throughout RENDER_REPORT" },
      { kind: "state", id: "FAILED", name: "FAILED", x: 640, y: 400, w: 260, h: 80, parent: "REWARDS_CONTROL",
        reactions: [ "ns / EXIT_CODE := 1  (SystemExit)" ] },
      { kind: "conn", id: "t1", c: "T", x: 1100, y: 520, parent: "REWARDS_CONTROL" }
    ],
    edges: [
      { from: "d0", to: "c1" },
      { from: "c1", to: "FETCHING", label: "[not OFFLINE]", lx: -10, ly: -8 },
      { from: "c1", to: "READING_SNAPSHOT", label: "[OFFLINE]", lx: -10, ly: 14 },
      { from: "FETCHING", to: "c2", label: "sp(FETCH_MONARCH)", lx: -10, ly: -18 },
      { from: "c2", to: "WRITING_SNAPSHOT", label: "[MONARCH_OK]", lx: 0, ly: 18 },
      { from: "c2", to: "FAILED", label: "[not MONARCH_OK]", lx: 95, ly: -30 },
      { from: "READING_SNAPSHOT", to: "c3", label: "sp(READ_SNAPSHOT)", lx: 0, ly: 16 },
      { from: "c3", to: "LOADING_RULES", label: "[SNAPSHOT_EXISTS]", lx: 0, ly: 14 },
      { from: "c3", to: "FAILED", label: "[not SNAPSHOT_EXISTS]", lx: -60, ly: 0 },
      { from: "WRITING_SNAPSHOT", to: "LOADING_RULES", label: "sp(WRITE_SNAPSHOT)", lx: 0, ly: -8 },
      { from: "LOADING_RULES", to: "c4", label: "sp(LOAD_RULES)", lx: -20, ly: 30 },
      { from: "c4", to: "ANALYZING", label: "[CARDS_RESOLVED]", lx: -20, ly: -12 },
      { from: "c4", to: "FAILED", label: "[not CARDS_RESOLVED]", lx: 0, ly: 14 },
      { from: "ANALYZING", to: "RENDERING", label: "sp(TRACK_CARD)", lx: 10, ly: 0 },
      { from: "RENDERING", to: "t1", label: "sp(RENDER_REPORT) / EXIT_CODE := 0", lx: 0, ly: 16 },
      { from: "FAILED", to: "t1" }
    ]
  }
};
