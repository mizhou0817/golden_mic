import Icon from './Icon';
import styles from './Primitives.module.css';
export default function IntroCard({ onStart, onSample }: { onStart: () => void; onSample: () => void }) {
  return <section className={styles.intro} aria-label="使用引导">
    <Icon name="intro" size={80} color="var(--ink)" />
    <div><h2 className={styles.title}>把稿子变成视频，不用学剪辑</h2>
      <p className={styles.introCopy}>写稿、传素材，AI 配音配画面，做好还能逐句改。</p>
      <div className={styles.actions}><button type="button" className={styles.button} data-primary="true" onClick={onStart}>开始写稿 →</button><button type="button" className={styles.button} onClick={onSample}>先看一条范例成片</button></div>
    </div>
  </section>;
}