import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AccessibilityInfo, Alert, Animated, Appearance, Platform, Pressable, SafeAreaView,
  ScrollView, Share, StyleSheet, Switch, Text, TextInput, useColorScheme, useWindowDimensions, View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Clipboard from 'expo-clipboard';
import { generateNumbers, validateSettings } from './src/random';
import {
  defaultSettings, HistoryItem, loadLocalData, saveHistory, saveSettings, SavedSettings, ThemeChoice,
} from './src/storage';

type Palette = typeof light;
const light = {
  bg: '#F7F4F6', surface: '#FFFFFF', surfaceAlt: '#EFE8EC', text: '#252126', muted: '#686068',
  border: '#D8CDD3', accent: '#B0005A', accentPress: '#870045', onAccent: '#FFFFFF', error: '#A2172B', tile: '#302A30', tileText: '#FFFFFF',
};
const dark: Palette = {
  bg: '#171417', surface: '#252125', surfaceAlt: '#342E33', text: '#FFF8FC', muted: '#C9BDC4',
  border: '#51464D', accent: '#F04A9B', accentPress: '#FF70B4', onAccent: '#1C0712', error: '#FF8B9B', tile: '#FFF7FB', tileText: '#2B1721',
};

export default function App() {
  const systemScheme = useColorScheme();
  const { width } = useWindowDimensions();
  const [settings, setSettings] = useState<SavedSettings>(defaultSettings);
  const [minText, setMinText] = useState('1');
  const [maxText, setMaxText] = useState('50');
  const [numbers, setNumbers] = useState<number[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const reveal = useRef(new Animated.Value(1)).current;
  const isDark = settings.theme === 'dark' || (settings.theme === 'system' && systemScheme === 'dark');
  const colors = isDark ? dark : light;
  const styles = useMemo(() => makeStyles(colors, width), [colors, width]);

  useEffect(() => {
    Promise.all([loadLocalData(), AccessibilityInfo.isReduceMotionEnabled()]).then(([data, reduced]) => {
      setSettings(data.settings); setMinText(String(data.settings.min)); setMaxText(String(data.settings.max));
      setHistory(data.history); setReduceMotion(reduced); setReady(true);
    });
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => subscription.remove();
  }, []);

  const updateSettings = (patch: Partial<SavedSettings>) => {
    setSettings(current => {
      const next = { ...current, ...patch };
      if (ready) void saveSettings(next);
      return next;
    });
  };

  const parsedSettings = () => ({
    ...settings,
    min: /^-?\d+$/.test(minText.trim()) ? Number(minText) : Number.NaN,
    max: /^-?\d+$/.test(maxText.trim()) ? Number(maxText) : Number.NaN,
  });

  const onGenerate = () => {
    const next = parsedSettings();
    const problem = validateSettings(next);
    if (problem) { setError(problem); AccessibilityInfo.announceForAccessibility(problem); return; }
    setError(null); updateSettings({ min: next.min, max: next.max });
    const generated = generateNumbers(next);
    setNumbers(generated);
    const item: HistoryItem = {
      id: `${Date.now()}-${generated.join('-')}`, numbers: generated, createdAt: new Date().toISOString(),
      min: next.min, max: next.max, allowDuplicates: next.allowDuplicates,
    };
    setHistory(current => {
      const updated = [item, ...current].slice(0, 20);
      void saveHistory(updated); return updated;
    });
    if (!reduceMotion) {
      reveal.setValue(0);
      Animated.spring(reveal, { toValue: 1, damping: 14, stiffness: 190, mass: 0.8, useNativeDriver: true }).start();
    }
    AccessibilityInfo.announceForAccessibility(`Generated ${generated.join(', ')}`);
  };

  const copy = async () => {
    if (!numbers.length) return;
    await Clipboard.setStringAsync(numbers.join(', '));
    AccessibilityInfo.announceForAccessibility('Numbers copied');
  };
  const share = async () => {
    if (!numbers.length) return;
    await Share.share({ message: `My random selection: ${numbers.join(', ')}` });
  };
  const clearSavedHistory = () => {
    setHistory([]);
    void saveHistory([]);
    AccessibilityInfo.announceForAccessibility('History cleared');
  };
  const clearHistory = () => {
    const message = 'This removes all saved sets from this device.';
    if (Platform.OS === 'web') {
      if (globalThis.confirm(`Clear history?\n\n${message}`)) clearSavedHistory();
      return;
    }
    Alert.alert('Clear history?', message, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Clear', style: 'destructive', onPress: clearSavedHistory },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <View style={styles.header}>
          <View style={styles.mark}><Text style={styles.markText} accessibilityElementsHidden>5</Text></View>
          <View style={styles.headerCopy}>
            <Text style={styles.title}>Five Random</Text>
            <Text style={styles.subtitle}>A fair, private number picker</Text>
          </View>
        </View>

        <View style={styles.hero}>
          <Text style={styles.eyebrow}>YOUR RANDOM SELECTION</Text>
          <Animated.View
            accessible accessibilityLabel={numbers.length ? `Results: ${numbers.join(', ')}` : 'No numbers generated yet'}
            style={[styles.tiles, { opacity: reveal, transform: [{ scale: reveal.interpolate({ inputRange: [0, 1], outputRange: [0.94, 1] }) }] }]}
          >
            {(numbers.length ? numbers : ['—', '—', '—', '—', '—']).map((value, index) => (
              <View key={index} style={styles.tile}><Text adjustsFontSizeToFit numberOfLines={1} style={styles.tileText}>{value}</Text></View>
            ))}
          </Animated.View>
          <Pressable
            accessibilityRole="button" accessibilityLabel="Generate 5 Numbers"
            onPress={onGenerate} style={({ pressed }) => [styles.primary, pressed && styles.primaryPressed]}
          ><Text style={styles.primaryText}>Generate 5 Numbers</Text></Pressable>
          <View style={styles.actions}>
            <SmallButton label="Copy all" onPress={copy} disabled={!numbers.length} styles={styles} />
            <SmallButton label="Share" onPress={share} disabled={!numbers.length} styles={styles} />
          </View>
          <Text style={styles.disclaimer}>Random selections only. This app does not predict outcomes or improve winning odds.</Text>
        </View>

        <Section title="Settings" styles={styles}>
          <View style={styles.inputRow}>
            <NumberInput label="Minimum" value={minText} onChangeText={setMinText} styles={styles} />
            <NumberInput label="Maximum" value={maxText} onChangeText={setMaxText} styles={styles} />
          </View>
          {error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          <ToggleRow label="Allow duplicates" hint="A number may appear more than once" value={settings.allowDuplicates} onChange={v => updateSettings({ allowDuplicates: v })} styles={styles} colors={colors} />
          <ToggleRow label="Sort ascending" hint="Show results from lowest to highest" value={settings.sortAscending} onChange={v => updateSettings({ sortAscending: v })} styles={styles} colors={colors} />
          <Text style={styles.fieldLabel}>Appearance</Text>
          <View style={styles.segment} accessibilityRole="radiogroup">
            {(['system', 'light', 'dark'] as ThemeChoice[]).map(theme => (
              <Pressable key={theme} accessibilityRole="radio" accessibilityState={{ selected: settings.theme === theme }}
                accessibilityLabel={`${theme} appearance`} onPress={() => updateSettings({ theme })}
                style={[styles.segmentItem, settings.theme === theme && styles.segmentActive]}>
                <Text style={[styles.segmentText, settings.theme === theme && styles.segmentTextActive]}>{theme[0]?.toUpperCase()}{theme.slice(1)}</Text>
              </Pressable>
            ))}
          </View>
        </Section>

        <Section title="Recent sets" styles={styles} action={history.length ? <Pressable accessibilityRole="button" onPress={clearHistory} hitSlop={12}><Text style={styles.clear}>Clear</Text></Pressable> : undefined}>
          {!history.length ? <Text style={styles.empty}>Your last 20 selections will appear here.</Text> : history.map(item => (
            <View key={item.id} style={styles.historyRow} accessible accessibilityLabel={`${item.numbers.join(', ')}. Range ${item.min} to ${item.max}. ${formatDate(item.createdAt)}`}>
              <View style={styles.historyNumbers}>{item.numbers.map((n, i) => <Text key={i} style={styles.historyNumber}>{n}{i < 4 ? '  ' : ''}</Text>)}</View>
              <Text style={styles.historyMeta}>{formatDate(item.createdAt)}  ·  {item.min}–{item.max}{item.allowDuplicates ? '  ·  duplicates on' : ''}</Text>
            </View>
          ))}
        </Section>
        <Text style={styles.footer}>Works offline · Stored only on this device</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, action, children, styles }: { title: string; action?: React.ReactNode; children: React.ReactNode; styles: ReturnType<typeof makeStyles> }) {
  return <View style={styles.section}><View style={styles.sectionHeader}><Text style={styles.sectionTitle}>{title}</Text>{action}</View>{children}</View>;
}
function NumberInput({ label, value, onChangeText, styles }: { label: string; value: string; onChangeText: (v: string) => void; styles: ReturnType<typeof makeStyles> }) {
  return <View style={styles.inputWrap}><Text style={styles.fieldLabel}>{label}</Text><TextInput accessibilityLabel={label} value={value} onChangeText={onChangeText} keyboardType={Platform.OS === 'ios' ? 'numbers-and-punctuation' : 'number-pad'} selectTextOnFocus style={styles.input} /></View>;
}
function ToggleRow({ label, hint, value, onChange, styles, colors }: { label: string; hint: string; value: boolean; onChange: (v: boolean) => void; styles: ReturnType<typeof makeStyles>; colors: Palette }) {
  return <View style={styles.toggleRow}><View style={styles.toggleCopy}><Text style={styles.toggleLabel}>{label}</Text><Text style={styles.toggleHint}>{hint}</Text></View><Switch accessibilityLabel={label} value={value} onValueChange={onChange} trackColor={{ false: colors.border, true: colors.accent }} thumbColor="#FFFFFF" /></View>;
}
function SmallButton({ label, onPress, disabled, styles }: { label: string; onPress: () => void; disabled: boolean; styles: ReturnType<typeof makeStyles> }) {
  return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} onPress={onPress} disabled={disabled} style={({ pressed }) => [styles.smallButton, disabled && styles.disabled, pressed && styles.smallPressed]}><Text style={styles.smallButtonText}>{label}</Text></Pressable>;
}
function formatDate(iso: string) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}

function makeStyles(c: Palette, width: number) {
  const narrow = width < 360;
  return StyleSheet.create({
    safe: { flex: 1, backgroundColor: c.bg }, page: { width: '100%', maxWidth: 640, alignSelf: 'center', padding: narrow ? 16 : 20, paddingBottom: 40, gap: 18 },
    header: { flexDirection: 'row', alignItems: 'center', marginTop: 8, marginBottom: 2 }, mark: { width: 46, height: 46, borderRadius: 15, backgroundColor: c.accent, alignItems: 'center', justifyContent: 'center' }, markText: { color: c.onAccent, fontWeight: '900', fontSize: 25 }, headerCopy: { marginLeft: 12 },
    title: { color: c.text, fontSize: 25, lineHeight: 31, fontWeight: '800', letterSpacing: -0.5 }, subtitle: { color: c.muted, fontSize: 14, lineHeight: 20 },
    hero: { backgroundColor: c.surface, borderRadius: 24, padding: narrow ? 16 : 20, borderWidth: 1, borderColor: c.border, shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 14, shadowOffset: { width: 0, height: 5 }, elevation: 3 },
    eyebrow: { color: c.accent, fontWeight: '800', fontSize: 12, letterSpacing: 1.3, marginBottom: 14 }, tiles: { flexDirection: 'row', gap: narrow ? 5 : 8, justifyContent: 'space-between', marginBottom: 18 },
    tile: { flex: 1, aspectRatio: 0.82, maxHeight: 84, minHeight: 62, borderRadius: 15, backgroundColor: c.tile, alignItems: 'center', justifyContent: 'center' }, tileText: { color: c.tileText, fontSize: narrow ? 24 : 30, fontWeight: '800', paddingHorizontal: 3 },
    primary: { minHeight: 56, borderRadius: 16, backgroundColor: c.accent, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 16 }, primaryPressed: { backgroundColor: c.accentPress, transform: [{ scale: 0.99 }] }, primaryText: { color: c.onAccent, fontSize: 17, fontWeight: '800' },
    actions: { flexDirection: 'row', gap: 10, marginTop: 10 }, smallButton: { flex: 1, minHeight: 48, borderWidth: 1, borderColor: c.border, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: c.surfaceAlt }, smallPressed: { opacity: 0.75 }, smallButtonText: { color: c.text, fontSize: 15, fontWeight: '700' }, disabled: { opacity: 0.42 }, disclaimer: { color: c.muted, fontSize: 12, lineHeight: 17, textAlign: 'center', marginTop: 12 },
    section: { backgroundColor: c.surface, borderRadius: 20, padding: narrow ? 16 : 18, borderWidth: 1, borderColor: c.border }, sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }, sectionTitle: { color: c.text, fontSize: 20, fontWeight: '800' }, clear: { color: c.accent, fontSize: 15, fontWeight: '700', padding: 6 },
    inputRow: { flexDirection: 'row', gap: 12 }, inputWrap: { flex: 1 }, fieldLabel: { color: c.text, fontSize: 14, lineHeight: 20, fontWeight: '700', marginBottom: 7 }, input: { color: c.text, backgroundColor: c.bg, borderWidth: 1, borderColor: c.border, borderRadius: 13, minHeight: 52, paddingHorizontal: 14, fontSize: 18, marginBottom: 12 }, error: { color: c.error, fontSize: 14, lineHeight: 20, fontWeight: '600', marginBottom: 8 },
    toggleRow: { minHeight: 66, flexDirection: 'row', alignItems: 'center', borderTopWidth: 1, borderTopColor: c.border, paddingVertical: 10 }, toggleCopy: { flex: 1, paddingRight: 12 }, toggleLabel: { color: c.text, fontSize: 16, lineHeight: 22, fontWeight: '700' }, toggleHint: { color: c.muted, fontSize: 13, lineHeight: 18 },
    segment: { flexDirection: 'row', padding: 4, backgroundColor: c.bg, borderRadius: 13, borderWidth: 1, borderColor: c.border }, segmentItem: { flex: 1, minHeight: 42, alignItems: 'center', justifyContent: 'center', borderRadius: 10 }, segmentActive: { backgroundColor: c.accent }, segmentText: { color: c.muted, fontSize: 14, fontWeight: '700' }, segmentTextActive: { color: c.onAccent },
    empty: { color: c.muted, fontSize: 15, lineHeight: 22, paddingBottom: 3 }, historyRow: { borderTopWidth: 1, borderTopColor: c.border, paddingVertical: 13 }, historyNumbers: { flexDirection: 'row', flexWrap: 'wrap' }, historyNumber: { color: c.text, fontSize: 18, lineHeight: 25, fontWeight: '800' }, historyMeta: { color: c.muted, fontSize: 12, lineHeight: 18, marginTop: 2 }, footer: { color: c.muted, textAlign: 'center', fontSize: 12, lineHeight: 18, marginTop: 2 },
  });
}
