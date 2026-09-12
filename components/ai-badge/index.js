Component({
  options: {
    addGlobalClass: true,
  },

  properties: {
    /** 默认文案即产品规则要求的显式标注 */
    text: { type: String, value: 'AI 整理 · 待确认' },
    /** normal | small */
    size: { type: String, value: 'normal' },
  },
});
