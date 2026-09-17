const { formatDuration } = require('../../utils/format');

Component({
  options: {
    addGlobalClass: true,
  },

  properties: {
    /** 原声时长（毫秒） */
    durationMs: { type: Number, value: 0 },
    /** 新故事仅提供原味口述 / 适合成书；旧故事仍可显示历史标签。 */
    mode: { type: String, value: '原味口述' },
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
