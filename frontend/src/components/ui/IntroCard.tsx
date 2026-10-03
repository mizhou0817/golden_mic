import Icon from './Icon';
import styles from './Primitives.module.css';
export default function IntroCard({ onStart, onSample }: { onStart: () => void; onSample: () => void }) {
  return <section className={styles.intro} aria-label="使用引导">
    <Icon name="intro" size={132} color="var(--ink)" />
    <div><h2 className={styles.title}>三步，把你的稿子变成一条新闻视频</h2>
      <ol className={styles.steps}><li><b>1 写稿</b> · 粘贴或现写</li><li><b>2 传素材</b> · 手机拍的就行</li><li><b>3 选效果</b> · 都有推荐</li><li><b>→ AI 剪好</b> · 通常几分钟</li></ol>
      <div className={styles.actions}><button type="button" className={styles.button} data-primary="true" onClick={onStart}>开始写稿 →</button><button type="button" className={styles.button} onClick={onSample}>先看一条范例成片</button></div>
    </div>
  </section>;
}