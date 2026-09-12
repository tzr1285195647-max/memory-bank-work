const { formatDuration } = require('../../utils/format');

Component({
  options: {
    addGlobalClass: true,
  },

  properties: {
    /** 原声时长（毫秒） */
    durationMs: { type: Number, value: 0 },
    /** 整理方式：自然整理 / 原味口述 / 适合成书 */
    mode: { type: String, value: '自然整理' },
    playable: { type: Boolean, value: false },
  },

  data: {
    durationText: '00:00',
  },

  observers: {
    durationMs(value) {
      this.setData({ durationText: formatDuration(value) });
    },
  },

  lifetimes: {
    attached() {
      this.setData({ durationText: formatDuration(this.data.durationMs) });
    },
  },

  methods: {
    onPlay() {
      if (!this.data.playable) return;
      this.triggerEvent('play');
    },
  },
});
