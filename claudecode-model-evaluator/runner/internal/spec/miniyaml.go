package spec

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

type miniYAMLParser struct {
	lines []string
	index int
}

func parseYAML(text string) (any, error) {
	p := &miniYAMLParser{lines: strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")}
	p.skipEmpty()
	if p.index >= len(p.lines) {
		return map[string]any{}, nil
	}
	value, err := p.parseBlock(p.indentOf(p.lines[p.index]))
	if err != nil {
		return nil, err
	}
	p.skipEmpty()
	if p.index < len(p.lines) {
		return nil, &SpecError{Message: fmt.Sprintf("Unexpected YAML content near line %d: %q", p.index+1, p.lines[p.index])}
	}
	return value, nil
}

func (p *miniYAMLParser) parseBlock(indent int) (any, error) {
	p.skipEmpty()
	if p.index >= len(p.lines) {
		return map[string]any{}, nil
	}
	line := p.lines[p.index]
	current := p.indentOf(line)
	if current < indent {
		return map[string]any{}, nil
	}
	if strings.HasPrefix(line[current:], "-") {
		return p.parseSequence(current)
	}
	return p.parseMapping(current)
}

func (p *miniYAMLParser) parseMapping(indent int) (map[string]any, error) {
	result := map[string]any{}
	for {
		p.skipEmpty()
		if p.index >= len(p.lines) {
			break
		}
		line := p.lines[p.index]
		current := p.indentOf(line)
		if current < indent {
			break
		}
		if current > indent {
			return nil, &SpecError{Message: fmt.Sprintf("Unexpected indentation on line %d: %q", p.index+1, line)}
		}
		stripped := line[current:]
		if strings.HasPrefix(stripped, "-") {
			break
		}
		key, rawValue, err := p.splitKeyValue(stripped)
		if err != nil {
			return nil, err
		}
		p.index++
		value, err := p.parseValueAfterKey(indent, rawValue)
		if err != nil {
			return nil, err
		}
		result[key] = value
	}
	return result, nil
}

func (p *miniYAMLParser) parseSequence(indent int) ([]any, error) {
	items := []any{}
	for {
		p.skipEmpty()
		if p.index >= len(p.lines) {
			break
		}
		line := p.lines[p.index]
		current := p.indentOf(line)
		if current < indent {
			break
		}
		if current > indent {
			return nil, &SpecError{Message: fmt.Sprintf("Unexpected indentation on line %d: %q", p.index+1, line)}
		}
		stripped := line[current:]
		if !strings.HasPrefix(stripped, "-") {
			break
		}
		tail := stripped[1:]
		if tail != "" && !strings.HasPrefix(tail, " ") {
			return nil, &SpecError{Message: fmt.Sprintf("Invalid list item on line %d: %q", p.index+1, line)}
		}
		p.index++
		item, err := p.parseSequenceItem(indent, strings.TrimSpace(tail))
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

func (p *miniYAMLParser) parseSequenceItem(indent int, itemText string) (any, error) {
	if itemText == "" {
		next := p.nextMeaningfulIndex(p.index)
		if next < 0 || p.indentOf(p.lines[next]) <= indent {
			return nil, nil
		}
		return p.parseBlock(p.indentOf(p.lines[next]))
	}
	if p.looksLikeMappingEntry(itemText) {
		key, rawValue, err := p.splitKeyValue(itemText)
		if err != nil {
			return nil, err
		}
		value, err := p.parseValueAfterKey(indent, rawValue)
		if err != nil {
			return nil, err
		}
		item := map[string]any{key: value}
		next := p.nextMeaningfulIndex(p.index)
		if next >= 0 && p.indentOf(p.lines[next]) > indent {
			extra, err := p.parseMapping(p.indentOf(p.lines[next]))
			if err != nil {
				return nil, err
			}
			for k, v := range extra {
				item[k] = v
			}
		}
		return item, nil
	}
	return p.parseInlineScalar(itemText), nil
}

func (p *miniYAMLParser) parseValueAfterKey(indent int, rawValue string) (any, error) {
	stripped := strings.TrimSpace(rawValue)
	if stripped == "" {
		next := p.nextMeaningfulIndex(p.index)
		if next < 0 || p.indentOf(p.lines[next]) <= indent {
			return nil, nil
		}
		return p.parseBlock(p.indentOf(p.lines[next]))
	}
	if stripped == "|" || stripped == ">" {
		return p.parseBlockScalar(indent, stripped == ">"), nil
	}
	return p.parseInlineScalar(stripped), nil
}

func (p *miniYAMLParser) parseBlockScalar(parentIndent int, folded bool) string {
	lines := []string{}
	blockIndent := -1
	for p.index < len(p.lines) {
		raw := p.lines[p.index]
		if strings.TrimSpace(raw) == "" {
			lines = append(lines, "")
			p.index++
			continue
		}
		current := p.indentOf(raw)
		if current <= parentIndent {
			break
		}
		if blockIndent < 0 {
			blockIndent = current
		}
		if current < blockIndent {
			break
		}
		lines = append(lines, raw[blockIndent:])
		p.index++
	}
	if !folded {
		return strings.TrimRight(strings.Join(lines, "\n"), "\n")
	}
	paragraphs := []string{}
	current := []string{}
	for _, line := range lines {
		if line == "" {
			if len(current) > 0 {
				paragraphs = append(paragraphs, strings.TrimSpace(strings.Join(current, " ")))
				current = nil
			}
			paragraphs = append(paragraphs, "")
			continue
		}
		current = append(current, line)
	}
	if len(current) > 0 {
		paragraphs = append(paragraphs, strings.TrimSpace(strings.Join(current, " ")))
	}
	return strings.TrimRight(strings.Join(paragraphs, "\n"), "\n")
}

func (p *miniYAMLParser) parseInlineScalar(value string) any {
	lowered := strings.ToLower(value)
	switch lowered {
	case "null", "~":
		return nil
	case "true":
		return true
	case "false":
		return false
	}
	if value == "{}" {
		return map[string]any{}
	}
	if value == "[]" {
		return []any{}
	}
	if regexp.MustCompile(`^-?\d+$`).MatchString(value) {
		if n, err := strconv.Atoi(value); err == nil {
			return n
		}
	}
	if regexp.MustCompile(`^-?\d+\.\d+$`).MatchString(value) {
		if n, err := strconv.ParseFloat(value, 64); err == nil {
			return n
		}
	}
	if strings.HasPrefix(value, `"`) && strings.HasSuffix(value, `"`) {
		replacer := strings.NewReplacer(`\n`, "\n", `\r`, "\r", `\t`, "\t", `\\`, `\\`, `\"`, `\"`)
		return replacer.Replace(value[1 : len(value)-1])
	}
	if strings.HasPrefix(value, `'`) && strings.HasSuffix(value, `'`) {
		return strings.ReplaceAll(value[1:len(value)-1], `''`, `'`)
	}
	return value
}

func (p *miniYAMLParser) splitKeyValue(text string) (string, string, error) {
	inSingle := false
	inDouble := false
	for i, char := range text {
		switch char {
		case '\'':
			if !inDouble {
				inSingle = !inSingle
			}
		case '"':
			if !inSingle {
				inDouble = !inDouble
			}
		case ':':
			if !inSingle && !inDouble {
				key := strings.TrimSpace(text[:i])
				if key != "" {
					return key, text[i+1:], nil
				}
				break
			}
		}
	}
	return "", "", &SpecError{Message: fmt.Sprintf("Invalid mapping entry near line %d: %q", p.index+1, text)}
}

func (p *miniYAMLParser) looksLikeMappingEntry(text string) bool {
	_, _, err := p.splitKeyValue(text)
	return err == nil
}

func (p *miniYAMLParser) nextMeaningfulIndex(index int) int {
	for index < len(p.lines) {
		stripped := strings.TrimSpace(p.lines[index])
		if stripped != "" && !strings.HasPrefix(stripped, "#") {
			return index
		}
		index++
	}
	return -1
}

func (p *miniYAMLParser) skipEmpty() {
	next := p.nextMeaningfulIndex(p.index)
	if next < 0 {
		p.index = len(p.lines)
	} else {
		p.index = next
	}
}
func (p *miniYAMLParser) indentOf(line string) int {
	prefix := line[:len(line)-len(strings.TrimLeft(line, " \t"))]
	if strings.Contains(prefix, "\t") {
		panic("Tabs are not supported in YAML indentation.")
	}
	return len(line) - len(strings.TrimLeft(line, " "))
}

func marshalYAML(value any, indent int) string {
	switch v := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		// preserve stable but readable order for known top-level keys.
		preferred := []string{"task", "target", "models", "execution", "rubric", "id", "prompt", "acceptance_notes", "allowed_paths", "repo_path", "setup_cmd", "test_cmd", "label", "launch_cmd", "launcher", "env", "timeout_minutes", "budget_usd", "artifacts_dir", "max_parallel", "workspace_mode", "profile", "type", "model", "max_turns", "extra_args"}
		ordered := []string{}
		used := map[string]bool{}
		for _, key := range preferred {
			if contains(keys, key) {
				ordered = append(ordered, key)
				used[key] = true
			}
		}
		for _, key := range keys {
			if !used[key] {
				ordered = append(ordered, key)
			}
		}
		parts := []string{}
		for _, key := range ordered {
			prefix := strings.Repeat(" ", indent) + key + ":"
			switch child := v[key].(type) {
			case map[string]any, []any:
				parts = append(parts, prefix+"\n"+marshalYAML(child, indent+2))
			case string:
				if strings.Contains(child, "\n") {
					block := strings.Repeat(" ", indent+2) + strings.ReplaceAll(child, "\n", "\n"+strings.Repeat(" ", indent+2))
					parts = append(parts, prefix+" |\n"+block)
				} else {
					parts = append(parts, prefix+" "+yamlScalar(child))
				}
			default:
				parts = append(parts, prefix+" "+yamlScalar(child))
			}
		}
		return strings.Join(parts, "\n")
	case []any:
		parts := []string{}
		for _, item := range v {
			switch child := item.(type) {
			case map[string]any:
				block := marshalYAML(child, indent+2)
				lines := strings.Split(block, "\n")
				if len(lines) == 0 {
					continue
				}
				first := strings.Repeat(" ", indent) + "- " + strings.TrimSpace(lines[0])
				rest := []string{first}
				for _, line := range lines[1:] {
					rest = append(rest, line)
				}
				parts = append(parts, strings.Join(rest, "\n"))
			case []any:
				parts = append(parts, strings.Repeat(" ", indent)+"-\n"+marshalYAML(child, indent+2))
			case string:
				parts = append(parts, strings.Repeat(" ", indent)+"- "+yamlScalar(child))
			default:
				parts = append(parts, strings.Repeat(" ", indent)+"- "+yamlScalar(child))
			}
		}
		return strings.Join(parts, "\n")
	default:
		return strings.Repeat(" ", indent) + yamlScalar(v)
	}
}

func yamlScalar(value any) string {
	switch v := value.(type) {
	case nil:
		return "null"
	case string:
		if v == "" {
			return `""`
		}
		if strings.ContainsAny(v, ":#[]{}&*!|>'\"\n\r\t") || strings.HasPrefix(v, "-") {
			return strconv.Quote(v)
		}
		return v
	case bool:
		if v {
			return "true"
		}
		return "false"
	default:
		return fmt.Sprintf("%v", value)
	}
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
