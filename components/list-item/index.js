Component({
  options: {
    addGlobalClass: true,
    multipleSlots: true,
  },

  properties: {
    /** story | topic | row */
    variant: { type: String, value: 'story' },
    tone: { type: String, value: 'white' },
    /** 序号，形如 '01'；传空则不显示 */
    index: { type: String, value: '' },
    /** topic 形态的字形，如「乡」「校」 */
    glyph: { type: String, value: '' },
    title: { type: String, value: '' },
    subtitle: { type: String, value: '' },
    /** '' | '›' | '→' | 'play' */
    arrow: { type: String, value: '' },
  },

  methods: {
    onTap() {
      this.triggerEvent('tap');
    },
  },
});
