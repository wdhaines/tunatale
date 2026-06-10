# TunaTale - Product Requirements Document
*"The best tuna on the net" - Meet your multilingual world-traveler companion*

## 1. Executive Summary

### Vision Statement
Create TunaTale, an AI-powered language learning app that generates personalized audio curricula from user-provided scripts and topics. Users get adaptive, audio-first immersion content that builds naturally toward their specific goals through comprehensible input principles, guided by an enthusiastic multilingual tuna who's traveled everywhere and has stories about everything.

### Success Criteria (Prototype Validation)
- **Norwegian Demo**: Build a system capable of supporting A2→B1 progression through personalized audio content
- **Tagalog Demo**: Demonstrate ground-up curriculum building from detailed user scripts (aspirational timeline)
- **Sustained Engagement**: Audio-first experience that maintains interest without gamification
- **Content Quality**: AI-generated stories that balance naturalness with pedagogical progression
- **Technical Feasibility**: Smooth audio pipeline with responsive learner controls

## 2. Problem Statement & Market Gap

### Market Gap
Current language learning solutions fall into distinct categories with clear limitations:
- **Audio courses** (Pimsleur): Highly effective methodology but completely static content
- **Comprehensible input apps** (LingQ, Language Reactor): Good immersion approach but text-first, limited to existing content
- **Spaced repetition** (Anki): Effective for retention but isolated flashcards lack natural context
- **Language podcasts**: Great for immersion but generic topics, no personalization
- **AI conversation apps**: Free-form practice but no structured curriculum building

**The Gap**: No solution combines AI-generated personalized content with audio-first immersion and pedagogically sound progression.

## 3. User Pain Points & Journeys

### Current User Journeys with Pain Points

**Audio Course User (Pimsleur)**
1. Starts structured audio lessons → *Works well initially*
2. Content becomes irrelevant to interests → *Motivation drops*
3. Can't adjust pace or focus areas → *Frustration builds*
4. Completes course with limited real-world applicability → *Limited transfer*

**Immersion Learner (Podcasts/YouTube)**
1. Finds interesting content in target language → *High motivation*
2. Content too difficult or too easy → *Comprehension issues*
3. No systematic vocabulary building → *Plateau effect*
4. No feedback on progress → *Uncertainty about improvement*

**SRS User (Anki)**
1. Creates or downloads flashcard decks → *Good systematic approach*
2. Reviews isolated words/phrases → *Lacks context*
3. Struggles to use learned vocabulary in conversation → *Transfer problem*
4. Deck becomes stale and repetitive → *Engagement drops*

## 4. Target User

Efficiency-focused language learners who prefer immersion and acquisition-based approaches over gamified apps. They value pedagogical effectiveness over engagement mechanics, want maximum language learning ROI per minute spent, and seek personalized content that adapts to their specific goals rather than generic cultural education.

## 5. Solution Overview

### Core Value Proposition
TunaTale is an AI-powered audio learning system that generates personalized curricula based on user-provided scripts, creating dynamic content streams that adapt to learning pace while embedding vocabulary in natural story contexts. This approach combines the effectiveness of comprehensible input with the personalization that only AI can provide.

**Expected Learning Outcomes** (supported by research):
- **Faster acquisition** through personalized, relevant content (Krashen's affective filter hypothesis)
- **Better retention** via spaced repetition in natural contexts (spacing effect research)
- **Improved transfer** through varied story contexts vs. isolated practice
- **Sustained motivation** through personally relevant content

### Key Differentiators
- **Dynamic content generation**: User provides scripts/topics, AI builds progressive curriculum
- **Audio-first immersion**: All content delivered as AI-generated speech, optimized for mobile use
- **Implicit SRS**: Vocabulary reinforcement through natural story contexts using FSRS algorithms
- **Adaptive pacing**: Uses learner control signals to adjust difficulty and speed
- **Syntactic unit focus**: Tracks collocations and meaningful chunks rather than isolated words

## 6. Pedagogical Approach

### Three-Phase Learning Cycle
TunaTale implements an evidence-based learning cycle that addresses research findings on graduated audio exposure with contextual support:

1. **Explicit Learning** (web app): Contextual preparation with key collocations, background information, and conceptual definitions without memorization pressure
2. **Audio Immersion** (mobile/car): Natural embedding of prepared vocabulary in story contexts through hands-free listening
3. **Spaced Reinforcement** (SRS): Progressive reinforcement in new contexts using implicit feedback signals

**Research Foundation:**
- Krashen's comprehensible input theory (i+1 formula for optimal learning)
- Research showing graduated audio exposure with preparation improves outcomes vs. immediate L2 immersion
- FSRS spaced repetition effectiveness for long-term retention
- Cognitive load theory for scaffolded learning design

This cycle addresses the finding that immediate L2 audio exposure without preparation can depress learning outcomes, while maintaining audio-first principles during the immersion phase.

## 7. Core Features

### 7.1 Curriculum Generation Engine
**Description:** AI creates personalized audio curricula from user-provided detailed scripts

**Functional Requirements:**
- Accept detailed user scripts as primary input (e.g., business presentations, travel conversations)
- Generate dynamic content streams that build toward the target script/goal
- Balance "what learners want" (their topics) with "what they need" (pedagogical progression)
- Break down complex content into syntactic units and collocations (3-5 word chunks based on corpus analysis)
- Create natural story contexts that embed target language organically
- Scale content to available learning time rather than fixed lesson durations

**Key Challenge:** Maintaining story naturalness while controlling pedagogical complexity - LLMs may need to introduce non-target syntactic units to maintain story authenticity

### 7.2 Adaptive Audio Delivery System
**Description:** Dynamic audio generation with sophisticated learner controls

**Functional Requirements:**
- Generate both content and audio using text-to-speech (standard TTS sufficient for prototype)
- **Target-language control phrases**: Train learners to use phrases like "Más despacio" for speed control, with visual feedback when commands are recognized
- **Conceptual definitions**: Provide native-language explanations that mirror target-language dictionary definitions rather than direct translations
- **Literal translations**: Show word-by-word or syntactic structure breakdowns on demand
- **Progressive interface training**: Start with visual controls, gradually teach voice commands
- Seamless audio playback optimized for mobile/car use with offline capability for downloaded sessions

### 7.3 Implicit Spaced Repetition System
**Description:** FSRS-based reinforcement through natural story contexts

**Functional Requirements:**
- Track syntactic units/collocations (3-5 words) rather than individual words
- Use learner help signals as SRS feedback:
  - No help needed = successful review
  - Translation/slowdown requests = failed review requiring more exposure
- Implement FSRS algorithms adapted for collocation tracking and implicit feedback
- Graduate units from SRS tracking when review frequency falls below natural occurrence rates
- Balance review content with new material introduction in dynamic content streams

**Technical Innovation:** Adapting FSRS for implicit feedback signals and syntactic units instead of explicit card reviews

### 7.4 Learning Modes
**Description:** Input/listening mode and output/practice mode using same underlying content

**Functional Requirements:**
- **Input/Listening Mode**: Continuous immersive stories with embedded target collocations
- **Output/Practice Mode**: Structured prompts for production practice ("say XYZ" without grading)
- User-controlled mode switching based on context and preference (not algorithmic)
- Same SRS backend and content generation, different prompts for content creation
- Combined lesson control and help mechanisms accessible via icons or target-language phrases

> **Refined design:** these modes have been broken into concrete learner postures (Review / Listen / Read / Generate / Produce) with per-mode decisions in `docs/learning-modes.md`. Read mode is the first build target.

## 8. Technical Feasibility

### 8.1 Simplified Implementation Approach
**TTS Strategy:** Start with standard TTS (Edge TTS) rather than neural - 4x cost savings with likely sufficient quality for language learning. Test and upgrade only if audio quality becomes a limiting factor.

**Speech Recognition:** Limited command vocabulary in target languages significantly reduces complexity compared to general speech recognition. Focus on predictable control phrases that learners practice repeatedly.

**Offline Architecture:** Decouple content generation from consumption - generate sessions online, download for offline playback. Eliminates need for complex offline AI processing while maintaining car-friendly reliability.

**LLM Integration:** Start with straightforward API calls and well-crafted prompts rather than enterprise-complexity architectures. Use mocking and caching to minimize costs during prototype phase.

### 8.2 Core Technology Stack
- **Frontend**: React Native for Android-first development with future iOS/web expansion
- **Audio**: Edge TTS for speech synthesis, native audio controls for car optimization
- **Backend**: Mocked responses transitioning to LLM APIs, local SQLite for progress tracking
- **Storage**: Local content caching for 30-minute sessions, minimal online dependency during use

### 8.3 Prototype Approach
**Phase 1: Fully Mocked Content**
- Pre-generated lesson sequences for Norwegian B1 and Tagalog A1
- Hardcoded content management, basic progress tracking
- Focus on audio pipeline and user experience validation

**Phase 2: Selective AI Integration**
- Live LLM integration for curriculum generation
- FSRS implementation for collocation tracking
- Iterative prompt refinement based on content quality

## 9. User Experience Design

### 9.1 Core User Journey
1. **Script Input**: User provides detailed script of target content (web app)
2. **Preparation Phase**: Optional explicit learning of key collocations and context (web app)
3. **Curriculum Generation**: AI creates progressive content stream
4. **Audio Immersion**: User progresses through adaptive audio content (mobile/car)
5. **Control Integration**: System teaches target-language control phrases
6. **Implicit Progress**: SRS adaptation based on help-seeking behavior

### 9.2 Interface Design Principles
**Android App (Primary):**
- Audio-first with minimal visual distraction, car-optimized controls
- Progressive training from visual icons to target-language voice commands
- Eyes-free interaction capability with haptic and audio feedback
- Simple, clean design without gamification elements

**Web App (Future):**
- Explicit learning phase with visual breakdowns, cultural context, conceptual definitions
- Content preparation and curriculum management
- Different design principles optimized for deeper exploration

**Mascot Integration:**
- TunaTale the enthusiastic multilingual tuna appears in preparation phases
- Provides context and stories from his travels to introduce new content
- Personality: slightly obnoxious but loveable, always has relevant stories from his global adventures

## 10. Success Metrics & Validation

### Prototype Validation Targets
- **Content Quality**: Generated stories feel natural while maintaining pedagogical progression
- **Technical Performance**: Smooth audio playback, responsive controls, reliable offline operation
- **Learning Effectiveness**: Personal validation through Norwegian and Tagalog demos
- **Adaptive Accuracy**: System successfully adjusts to learner help signals
- **Engagement**: Sustained use without external motivation systems

### Key Research Questions
- Can FSRS adapt effectively to implicit feedback signals from help requests?
- What collocation length (3-5 words) optimizes learning vs. naturalness through corpus analysis?
- How to balance story authenticity with controlled vocabulary introduction?

## 11. Business Model

### Approach
- **Personal Project**: Built for own use, shared with community of efficiency-focused language learners
- **Cost-Plus Model**: Cover infrastructure costs plus modest sustainability margin
- **Tipping Feature**: Optional contributions from users who find value
- **No Enterprise Ambitions**: Focus on pedagogical effectiveness over growth metrics

## 12. Risk Assessment

### Technical Risks
- **Content Quality**: AI-generated stories may lack pedagogical effectiveness or natural flow
- **FSRS Adaptation**: Unknown effectiveness of FSRS with implicit signals and syntactic units
- **Corpus Analysis**: Determining optimal collocation lengths and frequency thresholds requires research

### Mitigation Strategies
- **Iterative Content Testing**: Continuous refinement of LLM generation prompts based on personal usage
- **Empirical Validation**: Use corpus analysis to inform collocation and frequency decisions
- **Scrappy Development**: Focus on working prototypes over perfect solutions

## 13. Competitive Positioning

### Detailed Comparison
- **vs Pimsleur**: Dynamic, personalized content vs static lessons; maintains audio-first effectiveness
- **vs LingQ**: Audio-first immersion vs text-first reading; AI-generated vs existing content
- **vs Language Reactor**: Personalized content vs Netflix/YouTube dependency; structured progression vs random content
- **vs Anki**: Natural story contexts vs isolated flashcards; implicit vs explicit review
- **vs Glossika**: Personalized topics vs generic sentences; story-based vs repetitive drilling
- **vs Story Learning (Olly Richards)**: AI-generated vs pre-written stories; adaptive vs fixed progression
- **vs Language Podcasts**: User-chosen topics vs generic content; pedagogical progression vs entertainment focus

### Target User Migration
- **From Pimsleur**: Users frustrated with static content but love the audio methodology
- **From Language Podcasts**: Learners wanting structured progression with personally relevant content
- **From LingQ/Language Reactor**: Audio-first learners who prefer listening to reading
- **From Anki**: Users seeking more natural context for vocabulary learning

---

*TunaTale focuses on creating a sustainable, pedagogically sound alternative to existing language learning tools, prioritizing effectiveness over growth metrics and community value over profit maximization.*
